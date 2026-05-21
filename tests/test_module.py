import unittest
from decimal import Decimal
from unittest.mock import MagicMock


class TestAsyncPaymentModel(unittest.TestCase):
    """Unit tests for sale_async_payment module structure."""

    def test_states_definition(self):
        """Todos los estados requeridos están definidos."""
        from sale_async_payment.async_payment import STATES
        state_keys = [s[0] for s in STATES]
        for expected in ('pending', 'suggested', 'confirmed', 'expired', 'cancelled'):
            self.assertIn(expected, state_keys)

    def test_payment_methods_definition(self):
        """Todos los métodos de cobro asíncrono están definidos."""
        from sale_async_payment.async_payment import PAYMENT_METHODS
        method_keys = [m[0] for m in PAYMENT_METHODS]
        for expected in ('mp_link', 'mp_qr_static', 'bank_transfer', 'debin', 'other'):
            self.assertIn(expected, method_keys)

    def test_default_state_is_pending(self):
        """El estado por defecto de un cobro asíncrono es 'pending'."""
        from sale_async_payment.async_payment import AsyncPayment
        self.assertEqual(AsyncPayment.default_state(), 'pending')

    def test_transitions_include_required_paths(self):
        """Las transiciones de estados incluyen los caminos del diseño."""
        from sale_async_payment.async_payment import AsyncPayment
        transitions = AsyncPayment._transitions
        self.assertIn(('pending', 'suggested'), transitions)
        self.assertIn(('pending', 'confirmed'), transitions)
        self.assertIn(('suggested', 'confirmed'), transitions)
        self.assertIn(('pending', 'expired'), transitions)
        self.assertIn(('suggested', 'expired'), transitions)
        self.assertIn(('pending', 'cancelled'), transitions)
        self.assertIn(('suggested', 'cancelled'), transitions)
        # Reactivación desde expired
        self.assertIn(('expired', 'pending'), transitions)

    def test_configuration_defaults(self):
        """Los valores por defecto de vencimiento son razonables."""
        from sale_async_payment.configuration import AsyncPaymentConfig
        self.assertEqual(AsyncPaymentConfig.default_expiration_hours_mp_link(), 72)
        self.assertEqual(AsyncPaymentConfig.default_expiration_hours_bank_transfer(), 48)
        self.assertEqual(AsyncPaymentConfig.default_expiration_hours_debin(), 24)
        self.assertEqual(AsyncPaymentConfig.default_expiration_hours_other(), 48)


class TestAsyncToggles(unittest.TestCase):
    """Tests para los toggles enable_async_* en configs MP y QR (paso 4)."""

    def test_mp_config_async_defaults_are_false(self):
        """enable_async_transfer y enable_async_link en config MP son False por defecto."""
        from account_payment_mp.configuration import Configuration
        self.assertFalse(Configuration.default_enable_async_transfer())
        self.assertFalse(Configuration.default_enable_async_link())

    def test_qr_config_async_defaults_are_false(self):
        """enable_async_transfer y enable_async_debin en config QR son False por defecto."""
        from account_payment_qr.configuration import Configuration
        self.assertFalse(Configuration.default_enable_async_transfer())
        self.assertFalse(Configuration.default_enable_async_debin())


class TestSaleAsyncOverrides(unittest.TestCase):
    """Tests del paso 5: overrides en sale.sale."""

    def test_residual_effective_pending_plus_paid(self):
        """effective_residual_amount = total - paid - async_pending."""
        from sale_async_payment.sale import Sale
        sale = MagicMock()
        sale.id = 1
        # Async pending: 100 + 50 sugerido = 150. Confirmed/cancelled no cuentan.
        ap1 = MagicMock(state='pending', amount=Decimal('100'))
        ap2 = MagicMock(state='suggested', amount=Decimal('50'))
        ap3 = MagicMock(state='confirmed', amount=Decimal('200'))
        ap4 = MagicMock(state='cancelled', amount=Decimal('30'))
        sale.async_payments = [ap1, ap2, ap3, ap4]
        pending = Sale.get_async_pending_amount(
            [sale], 'async_pending_amount')
        self.assertEqual(pending[1], Decimal('150'))

        sale.total_amount = Decimal('1000')
        sale.paid_amount = Decimal('300')
        sale.async_pending_amount = pending[1]
        residual = Sale.get_effective_residual_amount(
            [sale], 'effective_residual_amount')
        self.assertEqual(residual[1], Decimal('550'))

    def test_cancel_blocked_when_async_confirmed(self):
        """Sale con async confirmed queda en blocked, sin nada en to_cascade."""
        from sale_async_payment.sale import Sale
        sale = MagicMock()
        sale.rec_name = 'V001'
        ap_confirmed = MagicMock(state='confirmed', amount=Decimal('100'))
        ap_pending = MagicMock(state='pending', amount=Decimal('50'))
        sale.async_payments = [ap_confirmed, ap_pending]

        blocked, to_cascade = Sale._async_classify_for_cancel([sale])
        self.assertEqual(blocked, [sale])
        self.assertEqual(to_cascade, [])

    def test_cancel_cascades_pending_and_suggested(self):
        """Sale sin async confirmed: pending y suggested van a to_cascade."""
        from sale_async_payment.sale import Sale
        sale = MagicMock()
        ap_p = MagicMock(state='pending', amount=Decimal('40'))
        ap_s = MagicMock(state='suggested', amount=Decimal('30'))
        ap_e = MagicMock(state='expired', amount=Decimal('20'))
        ap_c = MagicMock(state='cancelled', amount=Decimal('10'))
        sale.async_payments = [ap_p, ap_s, ap_e, ap_c]

        blocked, to_cascade = Sale._async_classify_for_cancel([sale])
        self.assertEqual(blocked, [])
        self.assertEqual(set(to_cascade), {ap_p, ap_s})


if __name__ == '__main__':
    unittest.main()
