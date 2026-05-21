from trytond.pool import Pool

from . import async_payment
from . import configuration
from . import mp_bridge
from . import qr_bridge
from . import sale
from . import user
from . import wizard


def register():
    Pool.register(
        async_payment.AsyncPayment,
        configuration.AsyncPaymentConfig,
        configuration.AsyncPaymentUserFilter,
        configuration.AsyncPaymentUserFilterShop,
        sale.Sale,
        user.User,
        wizard.SalePaymentForm,
        wizard.AsyncMethodSelectForm,
        wizard.AsyncConfirmForm,
        mp_bridge.MPTransaction,
        qr_bridge.QRDetection,
        module='sale_async_payment', type_='model')
    Pool.register(
        wizard.WizardSalePayment,
        module='sale_async_payment', type_='wizard')
