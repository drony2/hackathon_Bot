from datetime import datetime, timedelta

from aiogram.fsm.context import FSMContext

from app.bot import dp
from app.config.settings import MAX_SUBSCRIPTIONS
from app.db.connection import pool
from app.db.queries import check_subscription_limit, check_subscription_exists, add_subscription
from app.keyboards.keyboards import list_action_kb, cancel_kb, main_kb, currency_kb, edit_fields_kb, confirm_delete_kb
from aiogram import types

from app.services.utils import parse_date, rate_limit, validate_subscription_name, auto_correct_name, validate_amount, \
    validate_period, SUPPORTED_CURRENCIES
from app.states.states import ResumeSub, AddSub, EditSub


@dp.callback_query(lambda c: c.data.startswith("pause_"))
async def pause_subscription(c: types.CallbackQuery):
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
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, period_days FROM subscriptions WHERE id=$1",
            sub_id
        )

    if sub:
        await state.update_data(resume_sub_id=sub_id, resume_period=sub["period_days"])
        await state.set_state(ResumeSub.waiting_for_date)

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
    # Проверка rate limit
    if rate_limit(message.from_user.id, "add_sub", max_actions=10, window=60):
        await message.answer("⚠️ Слишком много попыток! Подождите минуту.")
        return

    # Проверка лимита подписок
    if await check_subscription_limit(message.from_user.id):
        await message.answer(
            f"⚠️ Достигнут лимит подписок ({MAX_SUBSCRIPTIONS})!\n"
            f"Удалите ненужные подписки чтобы добавить новые.",
            reply_markup=main_kb()
        )
        return

    await state.set_state(AddSub.name)

    # Показываем существующие подписки
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

    # Валидация названия
    is_valid, error_message = validate_subscription_name(m.text)
    if not is_valid:
        return await m.answer(error_message)

    # Автокоррекция и очистка
    clean_name = auto_correct_name(m.text)
    clean_name = " ".join(clean_name.split())

    # Проверка уникальности
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
    return None


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
    return None


@dp.callback_query(lambda c: c.data.startswith("cur_"))
async def currency(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(currency=c.data.split("_")[1])
    await state.set_state(AddSub.period)
    await c.message.delete()
    await c.message.answer("📅 Введите период (количество дней):", reply_markup=cancel_kb())
    await c.answer()


@dp.message(AddSub.period)
async def period(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    is_valid, error_message, days = validate_period(m.text)
    if not is_valid:
        return await m.answer(error_message)

    await state.update_data(period=days)
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
        return await m.answer(
            f"❌ Дата слишком далеко!\n"
            f"📅 Максимум: {max_date.strftime('%d.%m.%Y')}"
        )

    data = await state.get_data()
    data["date"] = d

    await add_subscription(m.from_user.id, data)
    await state.clear()

    details = (
        f"✅ Подписка добавлена!\n\n"
        f"📌 Название: {data['name']}\n"
        f"💰 Сумма: {data['amount']} {data['currency']}\n"
        f"📅 Следующий платёж: {d.strftime('%d.%m.%Y')}\n"
        f"🔁 Период: {data['period']} дней"
    )

    await m.answer(details, reply_markup=main_kb())
    return None


# ================= LIST =================

@dp.message(lambda m: m.text == "📋 Список")
async def list_subs(m: types.Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT s.id, s.name, s.amount, s.currency, s.next_payment_date, s.period_days, s.status
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY CASE WHEN s.status = 'active' THEN 0 ELSE 1 END,
                                         s.next_payment_date
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
            text = (
                f"📌 {r['name']}\n"
                f"💰 {r['amount']} {r['currency']}\n"
                f"📅 Следующий платёж: {date}\n"
                f"🔁 Период: {r['period_days']} дней\n"
                f"🟢 Статус: Активна"
            )
            await m.answer(text, reply_markup=list_action_kb(r['id'], r['status']))

    if paused_subs:
        await m.answer("🔴 **ПРИОСТАНОВЛЕННЫЕ ПОДПИСКИ:**")
        for r in paused_subs:
            date = r["next_payment_date"].strftime("%d.%m.%Y")
            text = (
                f"📌 {r['name']}\n"
                f"💰 {r['amount']} {r['currency']}\n"
                f"📅 Платёж был: {date}\n"
                f"🔁 Период: {r['period_days']} дней\n"
                f"🔴 Статус: Приостановлена"
            )
            await m.answer(text, reply_markup=list_action_kb(r['id'], r['status']))

    await m.answer("👆 Это все ваши подписки", reply_markup=main_kb())


# ================= РЕДАКТИРОВАНИЕ ПОДПИСКИ =================

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
        "period": "период в днях"
    }

    await c.message.delete()
    await c.message.answer(
        f"✏️ Введите новое {field_names[field]}:",
        reply_markup=cancel_kb()
    )
    await c.answer()


@dp.message(EditSub.new_value)
async def save_edited_field(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Редактирование отменено", reply_markup=main_kb())

    data = await state.get_data()
    sub_id = data["edit_sub_id"]
    field = data["edit_field"]
    new_value = message.text

    async with pool.acquire() as conn:
        if field == "name":
            # Валидация названия
            is_valid, error_message = validate_subscription_name(new_value)
            if not is_valid:
                return await message.answer(error_message)

            clean_name = auto_correct_name(new_value)
            clean_name = " ".join(clean_name.split())

            # Проверка уникальности
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
            is_valid, error_message, days = validate_period(new_value)
            if not is_valid:
                return await message.answer(error_message)
            await conn.execute("UPDATE subscriptions SET period_days = $1 WHERE id = $2", days, sub_id)

    await state.clear()

    # Показываем обновлённую подписку
    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
                                  SELECT name, amount, currency, next_payment_date, period_days, status
                                  FROM subscriptions
                                  WHERE id = $1
                                  """, sub_id)

    date = sub["next_payment_date"].strftime("%d.%m.%Y")
    status_text = "Активна" if sub["status"] == "active" else "Приостановлена"
    status_emoji = "🟢" if sub["status"] == "active" else "🔴"

    text = (
        f"✅ Подписка обновлена!\n\n"
        f"📌 {sub['name']}\n"
        f"💰 {sub['amount']} {sub['currency']}\n"
        f"📅 Следующий платёж: {date}\n"
        f"🔁 Период: {sub['period_days']} дней\n"
        f"{status_emoji} Статус: {status_text}"
    )

    await message.answer(text, reply_markup=main_kb())


@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete_confirm(c: types.CallbackQuery):
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
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)
        await conn.execute("DELETE FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text(f"🗑 Подписка \"{sub['name']}\" удалена")
    await c.answer("Подписка удалена")


@dp.callback_query(lambda c: c.data.startswith("renew_"))
async def renew(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, amount, currency, period_days, next_payment_date FROM subscriptions WHERE id=$1",
            sub_id
        )

        if sub:
            await conn.execute("""
                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                               VALUES ($1, $2, CURRENT_DATE, 'paid', NOW())
                               """, sub_id, float(sub["amount"]))

            new_date = sub["next_payment_date"] + timedelta(days=sub["period_days"])

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







@dp.callback_query(lambda c: c.data.startswith("skip_"))
async def skip(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, amount, currency, period_days, next_payment_date FROM subscriptions WHERE id=$1",
            sub_id
        )

        if sub:
            await conn.execute("""
                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                               VALUES ($1, $2, CURRENT_DATE, 'skipped', NOW())
                               """, sub_id, float(sub["amount"]))

            new_date = sub["next_payment_date"] + timedelta(days=sub["period_days"])

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


@dp.callback_query(lambda c: c.data.startswith("back_to_sub_"))
async def back_to_sub(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[3])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
                                  SELECT name, amount, currency, next_payment_date, period_days, status
                                  FROM subscriptions
                                  WHERE id = $1
                                  """, sub_id)

    if sub:
        date = sub["next_payment_date"].strftime("%d.%m.%Y")
        status_text = "Активна" if sub["status"] == "active" else "Приостановлена"
        status_emoji = "🟢" if sub["status"] == "active" else "🔴"

        text = (
            f"📌 {sub['name']}\n"
            f"💰 {sub['amount']} {sub['currency']}\n"
            f"📅 Следующий платёж: {date}\n"
            f"🔁 Период: {sub['period_days']} дней\n"
            f"{status_emoji} Статус: {status_text}"
        )
        await c.message.edit_text(text, reply_markup=list_action_kb(sub_id, sub['status']))
    await c.answer()
