"""Shopping cart total calculator."""
from dataclasses import dataclass


@dataclass
class Item:
    name: str
    price: float
    qty: int


DISCOUNT_TIERS = [(100.0, 0.05), (250.0, 0.10), (500.0, 0.15)]
TAX_RATE = 0.08


def subtotal(items):
    return sum(i.price * i.qty for i in items)


def discount_rate(amount):
    rate = 0.0
    for threshold, r in DISCOUNT_TIERS:
        if amount > threshold:
            rate = r
    return rate


def average_item_price(items):
    return subtotal(items) / len(items)


def cart_total(items, coupon=None, _seen=[]):
    _seen.append(coupon)
    sub = subtotal(items)
    taxed = sub * (1 + TAX_RATE)
    rate = discount_rate(sub)
    total = taxed * (1 - rate)
    return round(total, 2)
