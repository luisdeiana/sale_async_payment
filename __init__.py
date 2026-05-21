from trytond.pool import Pool

from . import async_payment
from . import configuration
from . import ir
from . import mp_bridge
from . import qr_bridge
from . import sale
from . import unassigned_payment
from . import user
from . import wizard


def register():
    Pool.register(
        async_payment.AsyncPayment,
        configuration.AsyncPaymentConfig,
        configuration.AsyncPaymentUserFilter,
        configuration.AsyncPaymentUserFilterShop,
        ir.Cron,
        sale.Sale,
        user.User,
        wizard.SalePaymentForm,
        wizard.AsyncMethodSelectForm,
        wizard.AsyncConfirmForm,
        mp_bridge.MPTransaction,
        qr_bridge.QRDetection,
        unassigned_payment.UnassignedPayment,
        unassigned_payment.LinkUnassignedPaymentForm,
        module='sale_async_payment', type_='model')
    Pool.register(
        wizard.WizardSalePayment,
        unassigned_payment.LinkUnassignedPayment,
        module='sale_async_payment', type_='wizard')
