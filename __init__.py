from trytond.pool import Pool

from . import async_payment
from . import configuration
from . import sale


def register():
    Pool.register(
        async_payment.AsyncPayment,
        configuration.AsyncPaymentConfig,
        configuration.AsyncPaymentUserFilter,
        configuration.AsyncPaymentUserFilterShop,
        sale.Sale,
        module='sale_async_payment', type_='model')
