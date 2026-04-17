from bot.database.repositories import BudgetRepository, PaymentRepository

async def check_budget_status(pool, telegram_id: int, currency: str):
    budget_repo = BudgetRepository(pool)
    payment_repo = PaymentRepository(pool)
    
    budget = await budget_repo.get_budget(telegram_id, currency)
    if not budget:
        return None
    
    spending = await payment_repo.get_monthly_spending(telegram_id, currency)
    
    total_spent = 0
    for row in spending:
        total_spent += float(row["total"])
    
    return {
        "currency": currency,
        "limit": float(budget),
        "spent": total_spent,
        "remaining": float(budget) - total_spent
    }