def can_redeem(credits, amount):
    return 0 < amount <= credits


def service_fee(tier):
    return 0


def redeem(actor, owner, amount, wallets):
    if actor != owner:
        raise PermissionError("owner required")
    available_credits = wallets[owner]
    if not can_redeem(available_credits, amount):
        raise ValueError("invalid amount")
    wallets[owner] = available_credits - amount
    return wallets[owner]
