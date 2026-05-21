from trytond.pool import Pool

from . import async_payment
from . import configuration
from . import sale
from . import wizard


def register():
    Pool.register(
        async_payment.AsyncPayment,
        configuration.AsyncPaymentConfig,
        configuration.AsyncPaymentUserFilter,
        configuration.AsyncPaymentUserFilterShop,
        sale.Sale,
        wizard.SalePaymentForm,
        wizard.AsyncMethodSelectForm,
        wizard.AsyncConfirmForm,
        module='sale_async_payment', type_='model')
    Pool.register(
        wizard.WizardSalePayment,
        module='sale_async_payment', type_='wizard')
