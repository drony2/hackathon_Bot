from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

from aiogram.fsm.context import FSMContext
from aiogram import types

from app.bot import dp
from app.config.settings import MAX_SUBSCRIPTIONS
from app.db.connection import get_pool
from app.db.queries import check_subscription_limit, check_subscription_exists, add_subscription
from app.keyboards.keyboards import (
    list_action_kb, cancel_kb, main_kb, currency_kb,
    edit_fields_kb, confirm_delete_kb, period_kb
)
from app.services.utils import (
    parse_date, rate_limit, validate_subscription_name,
    auto_correct_name, validate_amount, validate_period, SUPPORTED_CURRENCIES
)
from app.states.states import ResumeSub, AddSub, EditSub


def add_months(date: datetime, months: int) -> datetime:
    """
    Прибавляет месяцы к дате.
    Если исходная дата — последний день месяца, результат тоже будет последним днём месяца.
    """
    from dateutil.relativedelta import relativedelta

    # Проверяем, является ли дата последним днём месяца
    next_day = date + timedelta(days=1)
    is_last_day_of_month = next_day.day == 1

    result = date + relativedelta(months=months)

    # Если исходная дата была последним днём месяца,
    # делаем результат тоже последним днём месяца
    if is_last_day_of_month:
        # Прибавляем ещё месяц и отнимаем день
        result = (result + relativedelta(months=1)).replace(day=1) - timedelta(days=1)

    return result

def add_days(date: datetime, days: int) -> datetime:
    """Прибавляет дни к дате"""
    return date + timedelta(days=days)


# Словарь для преобразования периода в дни (для хранения в БД)
PERIOD_TO_DAYS = {
    "1month": 30,  # примерное, но для расчетов используется relativedelta
    "3month": 90,
    "6month": 180,
    "1year": 365,
}

PERIOD_TO_MONTHS = {
    "1month": 1,
    "3month": 3,
    "6month": 6,
    "1year": 12,
}

PERIOD_NAMES = {
    "1month": "1 месяц",
    "3month": "3 месяца",
    "6month": "6 месяцев",
    "1year": "1 год",
}


# ================= ОБРАБОТЧИКИ =================

@dp.callback_query(lambda c: c.data.startswith("pause_"))
async def pause_subscription(c: types.CallbackQuery):
    pool = get_pool()
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)

        if sub:
            await conn.execute("""
                               UPDATE subscriptions
                               SET status         = 'paused',
                                   reminded_3d    = FALSE,
                                   reminded_1d    = FALSE,
                                   reminded_today = FALSE
                               WHERE id = $1
                               """, sub_id)

            await c.message.edit_text(
                f"⏸ Подписка \"{sub['name']}\" приостановлена!\n"
                f"🔴 Уведомления отключены."
            )
            await c.answer("Подписка приостановлена")


@dp.callback_query(lambda c: c.data.startswith("resume_"))
async def resume_subscription_start(c: types.CallbackQuery, state: FSMContext):
    pool = get_pool()
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, period_days, period_type FROM subscriptions WHERE id=$1",
            sub_id
        )

    if sub:
        await state.update_data(
            resume_sub_id=sub_id,
            resume_period=sub["period_days"],
            resume_period_type=sub.get("period_type", "days")
        )
        await state.set_state(ResumeSub.waiting_for_date)

        # Вычисляем дату по умолчанию
        if sub.get("period_type") and sub["period_type"] in PERIOD_TO_MONTHS:
            months = PERIOD_TO_MONTHS[sub["period_type"]]
            default_date = add_months(datetime.now(), months).strftime("%d.%m.%Y")
        else:
            default_date = (datetime.now() + timedelta(days=sub["period_days"])).strftime("%d.%m.%Y")

        await c.message.delete()
        await c.message.answer(
            f"▶️ Возобновление подписки \"{sub['name']}\"\n\n"
            f"📅 Введите дату следующего платежа (YYYY-MM-DD или DD.MM.YYYY):\n"
            f"💡 Например: {default_date}",
            reply_markup=cancel_kb()
        )
    else:
        await c.answer("Подписка не найдена")

    await c.answer()


@dp.message(ResumeSub.waiting_for_date)
async def resume_subscription_finish(message: types.Message, state: FSMContext):
    pool = get_pool()
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Возобновление отменено", reply_markup=main_kb())

    d = parse_date(message.text)
    if not d:
        return await message.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или DD.MM.YYYY")

    today = datetime.now().date()
    if d < today:
        return await message.answer(
            f"❌ Нельзя указать прошедшую дату!\n"
            f"📅 Сегодня: {today.strftime('%d.%m.%Y')}\n"
            f"Пожалуйста, введите будущую дату:"
        )

    data = await state.get_data()
    sub_id = data["resume_sub_id"]

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)

        if sub:
            await conn.execute("""
                               UPDATE subscriptions
                               SET status            = 'active',
                                   next_payment_date = $1,
                                   reminded_3d       = FALSE,
                                   reminded_1d       = FALSE,
                                   reminded_today    = FALSE
                               WHERE id = $2
                               """, d, sub_id)

            await message.answer(
                f"▶️ Подписка \"{sub['name']}\" возобновлена!\n"
                f"📅 Следующий платёж: {d.strftime('%d.%m.%Y')}\n"
                f"🟢 Уведомления включены.",
                reply_markup=main_kb()
            )

    await state.clear()


@dp.message(lambda m: m.text == "➕ Добавить")
async def add(message: types.Message, state: FSMContext):
    pool = get_pool()
    if rate_limit(message.from_user.id, "add_sub", max_actions=10, window=60):
        await message.answer("⚠️ Слишком много попыток! Подождите минуту.")
        return

    if await check_subscription_limit(message.from_user.id):
        await message.answer(
            f"⚠️ Достигнут лимит подписок ({MAX_SUBSCRIPTIONS})!\n"
            f"Удалите ненужные подписки чтобы добавить новые.",
            reply_markup=main_kb()
        )
        return

    await state.set_state(AddSub.name)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT name
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY name
                                """, message.from_user.id)

    if rows:
        existing = "\n".join([f"• {r['name']}" for r in rows])
        text = (
            f"📝 Введите название подписки:\n\n"
            f"📋 Уже есть:\n{existing}\n\n"
            f"❌ Отмена - для отмены"
        )
    else:
        text = "📝 Введите название подписки:"

    await message.answer(text, reply_markup=cancel_kb())


@dp.message(AddSub.name)
async def name(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    is_valid, error_message = validate_subscription_name(m.text)
    if not is_valid:
        return await m.answer(error_message)

    clean_name = auto_correct_name(m.text)
    clean_name = " ".join(clean_name.split())

    exists = await check_subscription_exists(m.from_user.id, clean_name)
    if exists:
        return await m.answer(
            f"❌ У вас уже есть подписка с названием \"{clean_name}\"!\n"
            f"📝 Пожалуйста, введите другое название:"
        )

    await state.update_data(name=clean_name)
    await state.set_state(AddSub.amount)
    await m.answer(
        f"✅ Название: {clean_name}\n\n"
        f"💰 Введите сумму (например: 199.99 или 199):",
        reply_markup=cancel_kb()
    )


@dp.message(AddSub.amount)
async def amount(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Добавление отменено", reply_markup=main_kb())

    is_valid, error_message, amount_value = validate_amount(message.text)
    if not is_valid:
        return await message.answer(error_message)

    await state.update_data(amount=amount_value)
    await state.set_state(AddSub.currency)
    await message.answer("💱 Выберите валюту:", reply_markup=currency_kb())


@dp.callback_query(lambda c: c.data.startswith("cur_"))
async def currency(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(currency=c.data.split("_")[1])
    await state.set_state(AddSub.period)
    await c.message.delete()
    await c.message.answer("📅 Выберите период:", reply_markup=period_kb())
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("period_"))
async def period_selected(c: types.CallbackQuery, state: FSMContext):
    period_type = c.data.split("_")[1]  # 1month, 3month, 6month, 1year, custom

    if period_type == "custom":
        await state.set_state(AddSub.period_custom)
        await c.message.delete()
        await c.message.answer("📅 Введите период в днях:", reply_markup=cancel_kb())
    else:
        # Сохраняем тип периода и примерное количество дней
        period_days = PERIOD_TO_DAYS[period_type]
        await state.update_data(
            period=period_days,
            period_type=period_type
        )
        await state.set_state(AddSub.date)

        # Вычисляем дату по умолчанию
        months = PERIOD_TO_MONTHS[period_type]
        default_date = add_months(datetime.now(), months).strftime("%d.%m.%Y")

        await c.message.delete()
        await c.message.answer(
            f"✅ Период: {PERIOD_NAMES[period_type]}\n\n"
            f"📅 Введите дату следующего платежа (YYYY-MM-DD или DD.MM.YYYY):\n"
            f"💡 Например: {default_date}",
            reply_markup=cancel_kb()
        )

    await c.answer()


@dp.message(AddSub.period)
async def period_custom(m: types.Message, state: FSMContext):
    """Обработчик для своего периода (если state = period_custom)"""
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    is_valid, error_message, days = validate_period(m.text)
    if not is_valid:
        return await m.answer(error_message)

    await state.update_data(period=days, period_type="custom")
    await state.set_state(AddSub.date)

    default_date = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")
    await m.answer(
        f"📅 Введите дату следующего платежа (YYYY-MM-DD или DD.MM.YYYY):\n"
        f"💡 Например: {default_date}",
        reply_markup=cancel_kb()
    )


@dp.message(AddSub.date)
async def date(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    d = parse_date(m.text)
    if not d:
        return await m.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или DD.MM.YYYY")

    today = datetime.now().date()
    if d < today:
        return await m.answer(
            f"❌ Нельзя указать прошедшую дату!\n"
            f"📅 Сегодня: {today.strftime('%d.%m.%Y')}\n"
            f"📅 Вы ввели: {d.strftime('%d.%m.%Y')}\n\n"
            f"Пожалуйста, введите будущую дату:"
        )

    max_date = today + timedelta(days=365 * 5)
    if d > max_date:
        return await m.answer(f"❌ Дата слишком далеко!\n📅 Максимум: {max_date.strftime('%d.%m.%Y')}")

    data = await state.get_data()
    data["date"] = d

    await add_subscription(m.from_user.id, data)
    await state.clear()

    period_name = PERIOD_NAMES.get(data.get("period_type", ""), f"{data['period']} дней")

    details = (
        f"✅ Подписка добавлена!\n\n"
        f"📌 Название: {data['name']}\n"
        f"💰 Сумма: {data['amount']} {data['currency']}\n"
        f"📅 Следующий платёж: {d.strftime('%d.%m.%Y')}\n"
        f"🔁 Период: {period_name}"
    )

    await m.answer(details, reply_markup=main_kb())


# ================= LIST =================

@dp.message(lambda m: m.text == "📋 Список")
async def list_subs(m: types.Message):
    pool = get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT s.id,
                                       s.name,
                                       s.amount,
                                       s.currency,
                                       s.next_payment_date,
                                       s.period_days,
                                       s.status,
                                       s.period_type
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY CASE WHEN s.status = 'active' THEN 0 ELSE 1 END, s.next_payment_date
                                """, m.from_user.id)

    if not rows:
        await m.answer("📭 У вас пока нет подписок", reply_markup=main_kb())
        return

    active_subs = [r for r in rows if r["status"] == "active"]
    paused_subs = [r for r in rows if r["status"] != "active"]

    if active_subs:
        await m.answer("🟢 **АКТИВНЫЕ ПОДПИСКИ:**")
        for r in active_subs:
            date = r["next_payment_date"].strftime("%d.%m.%Y")
            period_name = PERIOD_NAMES.get(r.get("period_type", ""), f"{r['period_days']} дней")
            text = (
                f"📌 {r['name']}\n"
                f"💰 {r['amount']} {r['currency']}\n"
                f"📅 Следующий платёж: {date}\n"
                f"🔁 Период: {period_name}\n"
                f"🟢 Статус: Активна"
            )
            await m.answer(text, reply_markup=list_action_kb(r['id'], r['status']))

    if paused_subs:
        await m.answer("🔴 **ПРИОСТАНОВЛЕННЫЕ ПОДПИСКИ:**")
        for r in paused_subs:
            date = r["next_payment_date"].strftime("%d.%m.%Y")
            period_name = PERIOD_NAMES.get(r.get("period_type", ""), f"{r['period_days']} дней")
            text = (
                f"📌 {r['name']}\n"
                f"💰 {r['amount']} {r['currency']}\n"
                f"📅 Платёж был: {date}\n"
                f"🔁 Период: {period_name}\n"
                f"🔴 Статус: Приостановлена"
            )
            await m.answer(text, reply_markup=list_action_kb(r['id'], r['status']))

    await m.answer("👆 Это все ваши подписки", reply_markup=main_kb())


# ================= РЕДАКТИРОВАНИЕ =================

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_subscription(c: types.CallbackQuery, state: FSMContext):
    sub_id = int(c.data.split("_")[1])
    await state.update_data(edit_sub_id=sub_id)
    await c.message.edit_text(
        "✏️ Выберите поле для редактирования:",
        reply_markup=edit_fields_kb(sub_id)
    )
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("editfield_"))
async def edit_field(c: types.CallbackQuery, state: FSMContext):
    parts = c.data.split("_")
    sub_id = int(parts[1])
    field = parts[2]

    await state.update_data(edit_sub_id=sub_id, edit_field=field)
    await state.set_state(EditSub.new_value)

    field_names = {
        "name": "название",
        "amount": "сумму",
        "currency": "валюту (RUB/USD/EUR)",
        "period": "период (дни или 1month/3month/6month/1year)"
    }

    await c.message.delete()
    await c.message.answer(
        f"✏️ Введите новое {field_names[field]}:",
        reply_markup=cancel_kb()
    )
    await c.answer()


@dp.message(EditSub.new_value)
async def save_edited_field(message: types.Message, state: FSMContext):
    pool = get_pool()
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Редактирование отменено", reply_markup=main_kb())

    data = await state.get_data()
    sub_id = data["edit_sub_id"]
    field = data["edit_field"]
    new_value = message.text

    async with pool.acquire() as conn:
        if field == "name":
            is_valid, error_message = validate_subscription_name(new_value)
            if not is_valid:
                return await message.answer(error_message)

            clean_name = auto_correct_name(new_value)
            clean_name = " ".join(clean_name.split())

            user_id = await conn.fetchval("""
                                          SELECT u.telegram_id
                                          FROM users u
                                                   JOIN subscriptions s ON s.user_id = u.id
                                          WHERE s.id = $1
                                          """, sub_id)

            exists = await conn.fetchval("""
                                         SELECT EXISTS(SELECT 1
                                                       FROM subscriptions s
                                                                JOIN users u ON u.id = s.user_id
                                                       WHERE u.telegram_id = $1
                                                         AND LOWER(s.name) = LOWER($2)
                                                         AND s.id != $3)
                                         """, user_id, clean_name, sub_id)

            if exists:
                await state.clear()
                return await message.answer(
                    f"❌ У вас уже есть подписка с названием \"{clean_name}\"!\n"
                    f"📝 Редактирование отменено.",
                    reply_markup=main_kb()
                )

            await conn.execute("UPDATE subscriptions SET name = $1 WHERE id = $2", clean_name, sub_id)

        elif field == "amount":
            is_valid, error_message, amount_value = validate_amount(new_value)
            if not is_valid:
                return await message.answer(error_message)
            await conn.execute("UPDATE subscriptions SET amount = $1 WHERE id = $2", amount_value, sub_id)

        elif field == "currency":
            new_value = new_value.upper()
            if new_value not in SUPPORTED_CURRENCIES:
                return await message.answer(f"❗ Валюта должна быть: {', '.join(SUPPORTED_CURRENCIES.keys())}")
            await conn.execute("UPDATE subscriptions SET currency = $1 WHERE id = $2", new_value, sub_id)

        elif field == "period":
            # Проверяем, это готовый период или дни
            if new_value in PERIOD_TO_DAYS:
                period_type = new_value
                period_days = PERIOD_TO_DAYS[new_value]
                await conn.execute(
                    "UPDATE subscriptions SET period_days = $1, period_type = $2 WHERE id = $3",
                    period_days, period_type, sub_id
                )
            else:
                is_valid, error_message, days = validate_period(new_value)
                if not is_valid:
                    return await message.answer(error_message)
                await conn.execute(
                    "UPDATE subscriptions SET period_days = $1, period_type = NULL WHERE id = $2",
                    days, sub_id
                )

    await state.clear()

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
                                  SELECT name, amount, currency, next_payment_date, period_days, status, period_type
                                  FROM subscriptions
                                  WHERE id = $1
                                  """, sub_id)

    date = sub["next_payment_date"].strftime("%d.%m.%Y")
    status_text = "Активна" if sub["status"] == "active" else "Приостановлена"
    status_emoji = "🟢" if sub["status"] == "active" else "🔴"
    period_name = PERIOD_NAMES.get(sub.get("period_type", ""), f"{sub['period_days']} дней")

    text = (
        f"✅ Подписка обновлена!\n\n"
        f"📌 {sub['name']}\n"
        f"💰 {sub['amount']} {sub['currency']}\n"
        f"📅 Следующий платёж: {date}\n"
        f"🔁 Период: {period_name}\n"
        f"{status_emoji} Статус: {status_text}"
    )

    await message.answer(text, reply_markup=main_kb())


# ================= УДАЛЕНИЕ =================

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete_confirm(c: types.CallbackQuery):
    pool = get_pool()
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text(
        f"⚠️ Вы уверены, что хотите удалить подписку \"{sub['name']}\"?",
        reply_markup=confirm_delete_kb(sub_id)
    )
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("confirmdel_"))
async def delete_confirmed(c: types.CallbackQuery):
    pool = get_pool()
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)
        await conn.execute("DELETE FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text(f"🗑 Подписка \"{sub['name']}\" удалена")
    await c.answer("Подписка удалена")


# ================= ПРОДЛИТЬ =================

@dp.callback_query(lambda c: c.data.startswith("renew_"))
async def renew(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])
    pool = get_pool()

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, amount, currency, period_days, next_payment_date, period_type FROM subscriptions WHERE id=$1",
            sub_id
        )

        if sub:
            await conn.execute("""
                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                               VALUES ($1, $2, CURRENT_DATE, 'paid', NOW())
                               """, sub_id, float(sub["amount"]))

            # Вычисляем новую дату в зависимости от типа периода
            if sub.get("period_type") and sub["period_type"] in PERIOD_TO_MONTHS:
                months = PERIOD_TO_MONTHS[sub["period_type"]]
                new_date = add_months(sub["next_payment_date"], months)
            else:
                new_date = add_days(sub["next_payment_date"], sub["period_days"])

            await conn.execute("""
                               UPDATE subscriptions
                               SET next_payment_date = $1,
                                   reminded_3d       = FALSE,
                                   reminded_1d       = FALSE,
                                   reminded_today    = FALSE,
                                   status            = 'active'
                               WHERE id = $2
                               """, new_date, sub_id)

            await c.message.edit_text(
                f"✅ Подписка \"{sub['name']}\" продлена!\n"
                f"📅 Следующее списание: {new_date.strftime('%d.%m.%Y')}"
            )
            await c.answer("Подписка продлена")


# ================= ПРОПУСТИТЬ =================

@dp.callback_query(lambda c: c.data.startswith("skip_"))
async def skip(c: types.CallbackQuery):
    pool = get_pool()
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, amount, currency, period_days, next_payment_date, period_type FROM subscriptions WHERE id=$1",
            sub_id
        )

        if sub:
            await conn.execute("""
                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                               VALUES ($1, $2, CURRENT_DATE, 'skipped', NOW())
                               """, sub_id, float(sub["amount"]))

            if sub.get("period_type") and sub["period_type"] in PERIOD_TO_MONTHS:
                months = PERIOD_TO_MONTHS[sub["period_type"]]
                new_date = add_months(sub["next_payment_date"], months)
            else:
                new_date = add_days(sub["next_payment_date"], sub["period_days"])

            await conn.execute("""
                               UPDATE subscriptions
                               SET next_payment_date = $1,
                                   reminded_3d       = FALSE,
                                   reminded_1d       = FALSE,
                                   reminded_today    = FALSE
                               WHERE id = $2
                               """, new_date, sub_id)

            await c.message.edit_text(
                f"⏭️ Платёж по подписке \"{sub['name']}\" пропущен!\n"
                f"📅 Следующее списание: {new_date.strftime('%d.%m.%Y')}"
            )
            await c.answer("Платёж пропущен")


# ================= НАЗАД =================

@dp.callback_query(lambda c: c.data.startswith("back_to_sub_"))
async def back_to_sub(c: types.CallbackQuery):
    pool = get_pool()
    sub_id = int(c.data.split("_")[3])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
                                  SELECT name, amount, currency, next_payment_date, period_days, status, period_type
                                  FROM subscriptions
                                  WHERE id = $1
                                  """, sub_id)

    if sub:
        date = sub["next_payment_date"].strftime("%d.%m.%Y")
        status_text = "Активна" if sub["status"] == "active" else "Приостановлена"
        status_emoji = "🟢" if sub["status"] == "active" else "🔴"
        period_name = PERIOD_NAMES.get(sub.get("period_type", ""), f"{sub['period_days']} дней")

        text = (
            f"📌 {sub['name']}\n"
            f"💰 {sub['amount']} {sub['currency']}\n"
            f"📅 Следующий платёж: {date}\n"
            f"🔁 Период: {period_name}\n"
            f"{status_emoji} Статус: {status_text}"
        )
        await c.message.edit_text(text, reply_markup=list_action_kb(sub_id, sub['status']))
    await c.answer()