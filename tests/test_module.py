import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch


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


class TestWizardAsyncRegister(unittest.TestCase):
    """Tests del paso 6: wizard de cobro asíncrono."""

    def test_compute_next_state_partial_returns_start(self):
        """Si tras registrar pending sigue habiendo residual > 0 → 'start'."""
        from sale_async_payment.wizard import WizardSalePayment as W
        # Sale total=1000, paid=0, pending tras registro=400 → residual=600
        self.assertEqual(
            W._async_compute_next_state(
                Decimal('1000'), Decimal('0'), Decimal('400')),
            'start')

    def test_compute_next_state_total_returns_async_confirm(self):
        """Si tras registrar pending cubre todo el residual → 'async_confirm'."""
        from sale_async_payment.wizard import WizardSalePayment as W
        # 1000 - 0 - 1000 = 0 → cierre
        self.assertEqual(
            W._async_compute_next_state(
                Decimal('1000'), Decimal('0'), Decimal('1000')),
            'async_confirm')
        # 1000 - 300 - 700 = 0 → cierre
        self.assertEqual(
            W._async_compute_next_state(
                Decimal('1000'), Decimal('300'), Decimal('700')),
            'async_confirm')

    def test_validate_method_other_always_allowed(self):
        """El método 'other' nunca requiere toggle."""
        from sale_async_payment.wizard import WizardSalePayment as W
        # No flags y método 'other' → pasa sin UserError
        W._async_validate_method('other', {})

    def test_validate_method_requires_toggle(self):
        """mp_link sin toggle habilitado dispara UserError."""
        from sale_async_payment.wizard import WizardSalePayment as W
        from trytond.exceptions import UserError
        with self.assertRaises(UserError):
            W._async_validate_method('mp_link', {'mp_link': False})

    def test_register_mp_link_creates_transaction_and_links(self):
        """transition_async_register con mp_link crea mp.transaction y vincula
        mp_transaction en el async_payment recién creado."""
        from sale_async_payment.wizard import WizardSalePayment

        # Mocks de Tryton Pool
        async_payment_cls = MagicMock()
        new_async = MagicMock(id=42)
        async_payment_cls.create.return_value = [new_async]

        mp_config_cls = MagicMock()
        mp_transaction = MagicMock(id=99, payment_url='https://mp.test/link')
        mp_config_cls.create_checkout_pro.return_value = mp_transaction

        sale_cls = MagicMock()
        refreshed_sale = MagicMock()
        refreshed_sale.total_amount = Decimal('1000')
        refreshed_sale.paid_amount = Decimal('0')
        refreshed_sale.async_pending_amount = Decimal('1000')
        sale_cls.return_value = refreshed_sale

        def pool_get(model):
            return {
                'sale.async_payment': async_payment_cls,
                'sale.sale': sale_cls,
                'account.payment.mp.config': mp_config_cls,
            }[model]

        pool_mock = MagicMock()
        pool_mock.get.side_effect = pool_get

        # Wizard instance simulada: los classmethods se llaman a través del
        # mock como wrappers a la implementación real.
        wizard = MagicMock(spec=WizardSalePayment)
        wizard._async_validate_method = (
            lambda m, f: WizardSalePayment._async_validate_method(m, f))
        wizard._async_compute_next_state = (
            lambda t, p, pr: WizardSalePayment._async_compute_next_state(
                t, p, pr))

        # record (sale original) + forms del wizard
        sale_original = MagicMock(id=7)
        sale_original.shop = MagicMock(id=3)
        wizard.record = sale_original

        form = MagicMock()
        form.payment_method = 'mp_link'
        form.payment_amount = Decimal('1000')
        form.notes = None
        wizard.async_method_select = form

        journal = MagicMock(id=11, payment_method='mercadopago')
        wizard.start = MagicMock(journal=journal)

        with patch('sale_async_payment.wizard.Pool', return_value=pool_mock), \
             patch('sale_async_payment.wizard._get_journal_async_flags',
                   return_value={'mp_link': True}):
            next_state = WizardSalePayment.transition_async_register(wizard)

        # Aserts: se creó el async, se llamó create_checkout_pro y se vinculó
        async_payment_cls.create.assert_called_once()
        create_vals = async_payment_cls.create.call_args[0][0][0]
        self.assertEqual(create_vals['sale'], 7)
        self.assertEqual(create_vals['payment_method'], 'mp_link')
        self.assertEqual(create_vals['amount'], Decimal('1000'))
        self.assertEqual(create_vals['state'], 'pending')

        mp_config_cls.create_checkout_pro.assert_called_once_with(
            sale_original, 'sale.sale')
        async_payment_cls.write.assert_called_once_with(
            [new_async], {'mp_transaction': 99})

        # Cobertura total → cierre
        self.assertEqual(next_state, 'async_confirm')


class TestAsyncConfirmDiff(unittest.TestCase):
    """Tests del paso 7: cómputo de received_amount y diferencia."""

    def test_confirm_exact_received_equals_amount(self):
        """received_amount == amount → diff = 0."""
        from sale_async_payment.async_payment import AsyncPayment
        ap = MagicMock()
        ap.amount = Decimal('1000')
        ap.received_amount = Decimal('1000')
        received, diff = AsyncPayment._compute_received_and_diff(ap)
        self.assertEqual(received, Decimal('1000'))
        self.assertEqual(diff, Decimal('0'))

    def test_confirm_overpayment_positive_diff(self):
        """received_amount > amount → diff positivo (sobrante)."""
        from sale_async_payment.async_payment import AsyncPayment
        ap = MagicMock()
        ap.amount = Decimal('1000')
        ap.received_amount = Decimal('1050')
        received, diff = AsyncPayment._compute_received_and_diff(ap)
        self.assertEqual(received, Decimal('1050'))
        self.assertEqual(diff, Decimal('50'))

    def test_confirm_underpayment_negative_diff(self):
        """received_amount < amount → diff negativo (faltante)."""
        from sale_async_payment.async_payment import AsyncPayment
        ap = MagicMock()
        ap.amount = Decimal('1000')
        ap.received_amount = Decimal('970')
        received, diff = AsyncPayment._compute_received_and_diff(ap)
        self.assertEqual(received, Decimal('970'))
        self.assertEqual(diff, Decimal('-30'))

    def test_confirm_received_none_falls_back_to_amount(self):
        """Si received_amount es None, asume exacto (diff = 0)."""
        from sale_async_payment.async_payment import AsyncPayment
        ap = MagicMock()
        ap.amount = Decimal('500')
        ap.received_amount = None
        received, diff = AsyncPayment._compute_received_and_diff(ap)
        self.assertEqual(received, Decimal('500'))
        self.assertEqual(diff, Decimal('0'))


if __name__ == '__main__':
    unittest.main()
