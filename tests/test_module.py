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


class TestMPBridge(unittest.TestCase):
    """Tests del paso 8: bridge MP auto-confirma async."""

    def test_candidates_from_write_args_filters_state_and_line(self):
        """Solo writes que tocan state o statement_line entran como candidatos."""
        from sale_async_payment.mp_bridge import MPTransaction
        r1 = MagicMock(id=1)
        r2 = MagicMock(id=2)
        r3 = MagicMock(id=3)
        args = (
            [r1, r2], {'state': 'approved', 'date_approved': 'today'},
            [r3], {'description': 'algo'},  # NO toca state ni line → fuera
        )
        result = MPTransaction._candidates_from_write_args(args)
        self.assertEqual(result, {1, 2})

    def test_candidates_includes_statement_line_writes(self):
        """Writes que actualizan statement_line también entran."""
        from sale_async_payment.mp_bridge import MPTransaction
        r1 = MagicMock(id=10)
        args = ([r1], {'statement_line': 42})
        self.assertEqual(
            MPTransaction._candidates_from_write_args(args), {10})

    def test_is_ready_only_when_approved_and_has_line(self):
        """Disparo solo cuando approved Y statement_line != None."""
        from sale_async_payment.mp_bridge import MPTransaction
        # approved sin línea: no listo (webhook todavía no creó la línea)
        t1 = MagicMock(state='approved', statement_line=None)
        self.assertFalse(MPTransaction._is_transaction_ready_for_async(t1))
        # pending con línea: no listo
        t2 = MagicMock(state='pending', statement_line=MagicMock(id=1))
        self.assertFalse(MPTransaction._is_transaction_ready_for_async(t2))
        # approved con línea: listo
        t3 = MagicMock(state='approved', statement_line=MagicMock(id=1))
        self.assertTrue(MPTransaction._is_transaction_ready_for_async(t3))

    def test_auto_confirm_links_sale_and_marks_confirmed(self):
        """Cuando una transaction approved se vincula a un async pending,
        el bridge vincula sale en la línea (si falta) y marca el async
        como confirmed."""
        from sale_async_payment.mp_bridge import MPTransaction

        # Statement.line del webhook sin sale vinculada
        line = MagicMock(id=500, sale=None)
        txn = MagicMock(
            id=7, state='approved', statement_line=line,
            amount=Decimal('1000'))
        ap = MagicMock(
            id=42, state='pending',
            mp_transaction=txn,
            sale=MagicMock(id=99))

        async_payment_cls = MagicMock()
        stmt_line_cls = MagicMock()

        def pool_get(model):
            return {
                'sale.async_payment': async_payment_cls,
                'account.statement.line': stmt_line_cls,
            }[model]

        pool_mock = MagicMock()
        pool_mock.get.side_effect = pool_get

        txn_by_id = {7: txn}
        with patch('sale_async_payment.mp_bridge.Pool', return_value=pool_mock):
            MPTransaction._auto_confirm_linked_async([ap], txn_by_id)

        # Sale vinculada en la línea del webhook
        stmt_line_cls.write.assert_called_once_with([line], {'sale': 99})

        # Async marcado como confirmed con datos del txn
        async_payment_cls.write.assert_called_once()
        call_args = async_payment_cls.write.call_args[0]
        self.assertEqual(call_args[0], [ap])
        vals = call_args[1]
        self.assertEqual(vals['state'], 'confirmed')
        self.assertEqual(vals['statement_line'], 500)
        self.assertEqual(vals['received_amount'], Decimal('1000'))
        self.assertEqual(vals['match_criteria'], 'mp_payment_id')

    def test_auto_confirm_skips_sale_link_when_already_set(self):
        """Si la statement.line ya tiene sale, el bridge no la sobreescribe."""
        from sale_async_payment.mp_bridge import MPTransaction

        existing_sale = MagicMock(id=88)
        line = MagicMock(id=501, sale=existing_sale)
        txn = MagicMock(
            id=8, state='approved', statement_line=line,
            amount=Decimal('500'))
        ap = MagicMock(
            id=43, state='pending',
            mp_transaction=txn,
            sale=MagicMock(id=99))

        async_payment_cls = MagicMock()
        stmt_line_cls = MagicMock()

        def pool_get(model):
            return {
                'sale.async_payment': async_payment_cls,
                'account.statement.line': stmt_line_cls,
            }[model]

        pool_mock = MagicMock()
        pool_mock.get.side_effect = pool_get

        with patch('sale_async_payment.mp_bridge.Pool', return_value=pool_mock):
            MPTransaction._auto_confirm_linked_async([ap], {8: txn})

        stmt_line_cls.write.assert_not_called()
        async_payment_cls.write.assert_called_once()


class TestQRBridge(unittest.TestCase):
    """Tests del paso 8: bridge QR auto-confirma async."""

    def test_is_ready_only_when_confirmed(self):
        from sale_async_payment.qr_bridge import QRDetection
        d1 = MagicMock(state='confirmed')
        d2 = MagicMock(state='matched')
        d3 = MagicMock(state='pending')
        self.assertTrue(QRDetection._is_detection_ready_for_async(d1))
        self.assertFalse(QRDetection._is_detection_ready_for_async(d2))
        self.assertFalse(QRDetection._is_detection_ready_for_async(d3))

    def test_auto_confirm_links_async_with_detection_data(self):
        from sale_async_payment.qr_bridge import QRDetection

        line = MagicMock(id=601)
        det = MagicMock(
            id=15, state='confirmed', amount=Decimal('800'),
            bank_reference='REF-999', payer_name='Juan',
            payer_cuit='20-12345678-9', statement_line=line)
        ap = MagicMock(id=77, state='pending', qr_detection=det)

        async_payment_cls = MagicMock()
        pool_mock = MagicMock()
        pool_mock.get.return_value = async_payment_cls

        with patch('sale_async_payment.qr_bridge.Pool', return_value=pool_mock):
            QRDetection._auto_confirm_linked_async([ap], {15: det})

        async_payment_cls.write.assert_called_once()
        vals = async_payment_cls.write.call_args[0][1]
        self.assertEqual(vals['state'], 'confirmed')
        self.assertEqual(vals['received_amount'], Decimal('800'))
        self.assertEqual(vals['bank_reference'], 'REF-999')
        self.assertEqual(vals['payer_name'], 'Juan')
        self.assertEqual(vals['statement_line'], 601)
        self.assertEqual(vals['match_criteria'], 'bank_reference')


if __name__ == '__main__':
    unittest.main()
