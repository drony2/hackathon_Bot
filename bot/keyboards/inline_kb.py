from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def currency_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="₽ RUB", callback_data="cur_RUB"),
                InlineKeyboardButton(text="$ USD", callback_data="cur_USD"),
                InlineKeyboardButton(text="€ EUR", callback_data="cur_EUR")
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
        ]
    )

def budget_currency_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="₽ RUB", callback_data="budget_cur_RUB"),
                InlineKeyboardButton(text="$ USD", callback_data="budget_cur_USD"),
                InlineKeyboardButton(text="€ EUR", callback_data="budget_cur_EUR")
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
        ]
    )

def action_kb(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Продлить", callback_data=f"renew_{sub_id}"),
            InlineKeyboardButton(text="⏸ Приостановить", callback_data=f"pause_{sub_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{sub_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{sub_id}")
        ]
    ])

def list_action_kb(sub_id, status="active"):
    if status == "active":
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Продлить", callback_data=f"renew_{sub_id}"),
                InlineKeyboardButton(text="⏸ Приостановить", callback_data=f"pause_{sub_id}")
            ],
            [
                InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{sub_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{sub_id}")
            ]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"resume_{sub_id}")
            ],
            [
                InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{sub_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{sub_id}")
            ]
        ])

def edit_fields_kb(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Название", callback_data=f"editfield_{sub_id}_name")],
        [InlineKeyboardButton(text="💰 Сумма", callback_data=f"editfield_{sub_id}_amount")],
        [InlineKeyboardButton(text="💱 Валюта", callback_data=f"editfield_{sub_id}_currency")],
        [InlineKeyboardButton(text="📅 Период (дней)", callback_data=f"editfield_{sub_id}_period")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_sub_{sub_id}")]
    ])

def budget_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Установить лимит", callback_data="set_budget")],
        [InlineKeyboardButton(text="📊 Проверить статус", callback_data="check_budget")],
        [InlineKeyboardButton(text="📋 Все лимиты", callback_data="list_budgets")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ])

def confirm_delete_kb(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirmdel_{sub_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"back_to_sub_{sub_id}")
        ]
    ])