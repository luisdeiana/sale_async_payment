import datetime
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
        for expected in ('mp_link', 'mp_qr_static', 'bank_transfer', 'other'):
            self.assertIn(expected, method_keys)
        self.assertNotIn('debin', method_keys)

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
        self.assertEqual(AsyncPaymentConfig.default_default_expiration_value(), 48)
        self.assertEqual(AsyncPaymentConfig.default_default_expiration_unit(), 'hours')

    def test_configuration_line_default_unit_empty(self):
        """expiration_unit por defecto vacío (heredará del default global)."""
        from sale_async_payment.configuration import AsyncPaymentConfigLine
        self.assertEqual(AsyncPaymentConfigLine.default_expiration_unit(), '')

    def test_compute_expiration_date_uses_default_when_no_line(self):
        """compute_expiration_date sin líneas usa el default."""
        from sale_async_payment.configuration import AsyncPaymentConfig
        config = MagicMock()
        config.lines = []
        config.default_expiration_value = 24
        config.default_expiration_unit = 'hours'
        journal = MagicMock(id=11)
        before = datetime.datetime.now()
        result = AsyncPaymentConfig.compute_expiration_date(config, journal)
        after = datetime.datetime.now()
        self.assertGreaterEqual(result, before + datetime.timedelta(hours=24))
        self.assertLessEqual(result, after + datetime.timedelta(hours=24))

    def test_compute_expiration_date_uses_line_when_journal_matches(self):
        """compute_expiration_date con línea para el journal usa esa línea."""
        from sale_async_payment.configuration import AsyncPaymentConfig
        journal = MagicMock(id=11)
        line = MagicMock()
        line.journal = journal
        line.expiration_value = 72
        line.expiration_unit = 'hours'
        config = MagicMock()
        config.lines = [line]
        config.default_expiration_value = 48
        config.default_expiration_unit = 'hours'
        before = datetime.datetime.now()
        result = AsyncPaymentConfig.compute_expiration_date(config, journal)
        after = datetime.datetime.now()
        self.assertGreaterEqual(result, before + datetime.timedelta(hours=72))
        self.assertLessEqual(result, after + datetime.timedelta(hours=72))

    def test_compute_expiration_date_falls_back_when_journal_not_matched(self):
        """Si hay líneas pero ninguna para el journal recibido, usa el default."""
        from sale_async_payment.configuration import AsyncPaymentConfig
        other_journal = MagicMock(id=34)
        line = MagicMock()
        line.journal = other_journal
        line.expiration_value = 72
        line.expiration_unit = 'hours'
        config = MagicMock()
        config.lines = [line]
        config.default_expiration_value = 24
        config.default_expiration_unit = 'hours'
        target_journal = MagicMock(id=99)
        before = datetime.datetime.now()
        result = AsyncPaymentConfig.compute_expiration_date(
            config, target_journal)
        after = datetime.datetime.now()
        self.assertGreaterEqual(result, before + datetime.timedelta(hours=24))
        self.assertLessEqual(result, after + datetime.timedelta(hours=24))

    def test_compute_expiration_date_days_unit(self):
        """compute_expiration_date con unidad 'days' convierte correctamente."""
        from sale_async_payment.configuration import AsyncPaymentConfig
        config = MagicMock()
        config.lines = []
        config.default_expiration_value = 2
        config.default_expiration_unit = 'days'
        journal = MagicMock(id=11)
        before = datetime.datetime.now()
        result = AsyncPaymentConfig.compute_expiration_date(config, journal)
        after = datetime.datetime.now()
        self.assertGreaterEqual(result, before + datetime.timedelta(days=2))
        self.assertLessEqual(result, after + datetime.timedelta(days=2))


class TestAsyncCapableMethods(unittest.TestCase):
    """Tests para la lista blanca ASYNC_CAPABLE_METHODS y derivación de método."""

    def test_async_capable_methods_includes_initial_two(self):
        """La lista blanca inicial incluye mercadopago y bank_polling."""
        from sale_async_payment.async_payment import ASYNC_CAPABLE_METHODS
        self.assertIn('mercadopago', ASYNC_CAPABLE_METHODS)
        self.assertIn('bank_polling', ASYNC_CAPABLE_METHODS)

    def test_journal_to_async_method_mapping(self):
        """El mapeo journal.payment_method → método async es consistente
        con PAYMENT_METHODS."""
        from sale_async_payment.async_payment import (
            ASYNC_CAPABLE_METHODS, JOURNAL_TO_ASYNC_METHOD, PAYMENT_METHODS)
        method_keys = {m[0] for m in PAYMENT_METHODS}
        for journal_pm in ASYNC_CAPABLE_METHODS:
            self.assertIn(journal_pm, JOURNAL_TO_ASYNC_METHOD,
                f"Falta mapeo para journal.payment_method '{journal_pm}'")
            self.assertIn(JOURNAL_TO_ASYNC_METHOD[journal_pm], method_keys,
                f"El método async derivado para '{journal_pm}' no está en "
                f"PAYMENT_METHODS")

    def test_derive_async_method_for_mp(self):
        """Journal mercadopago → método 'mp_link'."""
        from sale_async_payment.wizard import _derive_async_method
        journal = MagicMock(payment_method='mercadopago')
        self.assertEqual(_derive_async_method(journal), 'mp_link')

    def test_derive_async_method_for_bank(self):
        """Journal bank_polling → método 'bank_transfer'."""
        from sale_async_payment.wizard import _derive_async_method
        journal = MagicMock(payment_method='bank_polling')
        self.assertEqual(_derive_async_method(journal), 'bank_transfer')

    def test_derive_async_method_fallback_other(self):
        """Journal con payment_method sin mapeo → 'other'."""
        from sale_async_payment.wizard import _derive_async_method
        journal = MagicMock(payment_method='unknown_method')
        self.assertEqual(_derive_async_method(journal), 'other')


class TestConfigLineJournalValidation(unittest.TestCase):
    """Tests para validación de journal en sale.async_payment.config.line."""

    def test_check_journal_async_capable_accepts_mp(self):
        """Journal con payment_method='mercadopago' pasa la validación."""
        from sale_async_payment.configuration import AsyncPaymentConfigLine
        line = MagicMock()
        line.journal = MagicMock(payment_method='mercadopago', name='MP JNL')
        AsyncPaymentConfigLine.check_journal_async_capable(line)

    def test_check_journal_async_capable_accepts_bank(self):
        """Journal con payment_method='bank_polling' pasa la validación."""
        from sale_async_payment.configuration import AsyncPaymentConfigLine
        line = MagicMock()
        line.journal = MagicMock(payment_method='bank_polling', name='BNA')
        AsyncPaymentConfigLine.check_journal_async_capable(line)

    def test_check_journal_async_capable_rejects_none(self):
        """Journal con payment_method='none' dispara UserError descriptivo."""
        from sale_async_payment.configuration import AsyncPaymentConfigLine
        from trytond.exceptions import UserError
        line = MagicMock()
        line.journal = MagicMock(payment_method='none', name='Caja Efectivo')
        with self.assertRaises(UserError) as ctx:
            AsyncPaymentConfigLine.check_journal_async_capable(line)
        self.assertIn('Caja Efectivo', str(ctx.exception))
        self.assertIn('no admite cobros asíncronos', str(ctx.exception))

    def test_check_journal_async_capable_rejects_unknown(self):
        """Journal con payment_method desconocido dispara UserError."""
        from sale_async_payment.configuration import AsyncPaymentConfigLine
        from trytond.exceptions import UserError
        line = MagicMock()
        line.journal = MagicMock(payment_method='openpay', name='Openpay JNL')
        with self.assertRaises(UserError):
            AsyncPaymentConfigLine.check_journal_async_capable(line)

    def test_check_journal_async_capable_ignores_empty_journal(self):
        """Si no hay journal seteado, no se valida (otro check exigirá required=True)."""
        from sale_async_payment.configuration import AsyncPaymentConfigLine
        line = MagicMock()
        line.journal = None
        AsyncPaymentConfigLine.check_journal_async_capable(line)


class TestHasAsyncConfig(unittest.TestCase):
    """Tests para _journal_has_async_config: el botón async solo aparece
    si el journal tiene línea configurada."""

    def test_no_journal_returns_false(self):
        from sale_async_payment.wizard import _journal_has_async_config
        self.assertFalse(_journal_has_async_config(None))

    def test_journal_with_non_capable_method_returns_false(self):
        """Journal cuyo payment_method NO está en ASYNC_CAPABLE_METHODS → False
        sin siquiera consultar config.line."""
        from sale_async_payment.wizard import _journal_has_async_config
        journal = MagicMock(payment_method='none', id=99)
        self.assertFalse(_journal_has_async_config(journal))

    def test_capable_method_without_config_line_returns_false(self):
        """Journal con payment_method capable pero sin línea en config.line
        → False (la grilla es la fuente de la verdad)."""
        from sale_async_payment import wizard as wiz
        journal = MagicMock(payment_method='mercadopago', id=11)
        config_line_cls = MagicMock()
        config_line_cls.search.return_value = []
        pool_mock = MagicMock()
        pool_mock.get.return_value = config_line_cls
        with patch.object(wiz, 'Pool', return_value=pool_mock):
            self.assertFalse(wiz._journal_has_async_config(journal))
        config_line_cls.search.assert_called_once_with(
            [('journal', '=', 11)], limit=1)

    def test_capable_method_with_config_line_returns_true(self):
        """Journal con payment_method capable + línea configurada → True."""
        from sale_async_payment import wizard as wiz
        journal = MagicMock(payment_method='bank_polling', id=34)
        config_line_cls = MagicMock()
        config_line_cls.search.return_value = [MagicMock(id=1)]
        pool_mock = MagicMock()
        pool_mock.get.return_value = config_line_cls
        with patch.object(wiz, 'Pool', return_value=pool_mock):
            self.assertTrue(wiz._journal_has_async_config(journal))


class TestSalePaymentFormAsyncOptions(unittest.TestCase):
    """Tests para SalePaymentForm.on_change_with_has_async_options."""

    def test_no_journal_returns_false(self):
        from sale_async_payment.wizard import SalePaymentForm
        form = MagicMock()
        form.journal = None
        self.assertFalse(
            SalePaymentForm.on_change_with_has_async_options(form))

    def test_journal_without_line_returns_false(self):
        """on_change_with_has_async_options delega en _journal_has_async_config
        — si no hay línea, el botón asíncrono no aparece."""
        from sale_async_payment.wizard import SalePaymentForm
        form = MagicMock()
        form.journal = MagicMock(payment_method='mercadopago', id=11)
        with patch(
                'sale_async_payment.wizard._journal_has_async_config',
                return_value=False) as has_mock:
            result = SalePaymentForm.on_change_with_has_async_options(form)
        self.assertFalse(result)
        has_mock.assert_called_once_with(form.journal)

    def test_journal_with_line_returns_true(self):
        from sale_async_payment.wizard import SalePaymentForm
        form = MagicMock()
        form.journal = MagicMock(payment_method='bank_polling', id=34)
        with patch(
                'sale_async_payment.wizard._journal_has_async_config',
                return_value=True):
            self.assertTrue(
                SalePaymentForm.on_change_with_has_async_options(form))


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

    def test_validate_journal_raises_without_config_line(self):
        """_async_validate_journal dispara UserError si no hay línea
        en config.line para el journal."""
        from sale_async_payment.wizard import WizardSalePayment as W
        from trytond.exceptions import UserError
        journal = MagicMock(name='Caja', payment_method='none', id=99)
        with patch(
                'sale_async_payment.wizard._journal_has_async_config',
                return_value=False):
            with self.assertRaises(UserError):
                W._async_validate_journal(journal)

    def test_validate_journal_passes_when_has_config_line(self):
        """_async_validate_journal pasa sin error si _journal_has_async_config
        retorna True."""
        from sale_async_payment.wizard import WizardSalePayment as W
        journal = MagicMock(name='MP', payment_method='mercadopago', id=11)
        with patch(
                'sale_async_payment.wizard._journal_has_async_config',
                return_value=True):
            W._async_validate_journal(journal)  # No raise

    def test_register_mp_link_creates_transaction_and_links(self):
        """transition_async_register con journal MP deriva 'mp_link', crea
        mp.transaction y vincula mp_transaction en el async_payment."""
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

        async_config_cls = MagicMock()
        async_config_instance = MagicMock()
        async_config_instance.compute_expiration_date.return_value = (
            datetime.datetime(2026, 6, 1, 12, 0))
        async_config_cls.return_value = async_config_instance

        def pool_get(model):
            return {
                'sale.async_payment': async_payment_cls,
                'sale.sale': sale_cls,
                'account.payment.mp.config': mp_config_cls,
                'sale.async_payment.config': async_config_cls,
            }[model]

        pool_mock = MagicMock()
        pool_mock.get.side_effect = pool_get

        wizard = MagicMock(spec=WizardSalePayment)
        wizard._async_validate_journal = (
            lambda j: WizardSalePayment._async_validate_journal(j))
        wizard._async_compute_next_state = (
            lambda t, p, pr: WizardSalePayment._async_compute_next_state(
                t, p, pr))

        # record (sale original) + forms del wizard
        sale_original = MagicMock(id=7)
        sale_original.shop = MagicMock(id=3)
        wizard.record = sale_original

        form = MagicMock()
        # El form ya NO tiene payment_method — se deriva del journal
        form.payment_amount = Decimal('1000')
        form.notes = None
        wizard.async_method_select = form

        journal = MagicMock(id=11, payment_method='mercadopago')
        wizard.start = MagicMock(journal=journal)

        with patch('sale_async_payment.wizard.Pool', return_value=pool_mock), \
             patch('sale_async_payment.wizard._journal_has_async_config',
                   return_value=True):
            next_state = WizardSalePayment.transition_async_register(wizard)

        # Aserts: método derivado del journal, async creado y mp_transaction vinculada
        async_payment_cls.create.assert_called_once()
        create_vals = async_payment_cls.create.call_args[0][0][0]
        self.assertEqual(create_vals['sale'], 7)
        self.assertEqual(create_vals['payment_method'], 'mp_link')
        self.assertEqual(create_vals['amount'], Decimal('1000'))
        self.assertEqual(create_vals['journal'], 11)
        self.assertEqual(create_vals['state'], 'pending')

        mp_config_cls.create_checkout_pro.assert_called_once_with(
            sale_original, 'sale.sale')
        async_payment_cls.write.assert_called_once_with(
            [new_async], {'mp_transaction': 99})

        # Cobertura total → cierre
        self.assertEqual(next_state, 'async_confirm')

    def test_register_bank_polling_derives_bank_transfer(self):
        """transition_async_register con journal bank_polling deriva
        'bank_transfer' y NO llama a create_checkout_pro."""
        from sale_async_payment.wizard import WizardSalePayment

        async_payment_cls = MagicMock()
        new_async = MagicMock(id=43)
        async_payment_cls.create.return_value = [new_async]

        sale_cls = MagicMock()
        refreshed_sale = MagicMock()
        refreshed_sale.total_amount = Decimal('500')
        refreshed_sale.paid_amount = Decimal('0')
        refreshed_sale.async_pending_amount = Decimal('500')
        sale_cls.return_value = refreshed_sale

        async_config_cls = MagicMock()
        async_config_instance = MagicMock()
        async_config_instance.compute_expiration_date.return_value = (
            datetime.datetime(2026, 6, 1, 12, 0))
        async_config_cls.return_value = async_config_instance

        mp_config_cls = MagicMock()

        def pool_get(model):
            return {
                'sale.async_payment': async_payment_cls,
                'sale.sale': sale_cls,
                'sale.async_payment.config': async_config_cls,
                'account.payment.mp.config': mp_config_cls,
            }[model]

        pool_mock = MagicMock()
        pool_mock.get.side_effect = pool_get

        wizard = MagicMock(spec=WizardSalePayment)
        wizard._async_validate_journal = (
            lambda j: WizardSalePayment._async_validate_journal(j))
        wizard._async_compute_next_state = (
            lambda t, p, pr: WizardSalePayment._async_compute_next_state(
                t, p, pr))

        sale_original = MagicMock(id=8)
        sale_original.shop = None
        wizard.record = sale_original

        form = MagicMock()
        form.payment_amount = Decimal('500')
        form.notes = 'Espera transferencia BNA'
        wizard.async_method_select = form

        journal = MagicMock(id=34, payment_method='bank_polling')
        wizard.start = MagicMock(journal=journal)

        with patch('sale_async_payment.wizard.Pool', return_value=pool_mock), \
             patch('sale_async_payment.wizard._journal_has_async_config',
                   return_value=True):
            WizardSalePayment.transition_async_register(wizard)

        create_vals = async_payment_cls.create.call_args[0][0][0]
        self.assertEqual(create_vals['payment_method'], 'bank_transfer')
        # Sin link MP → no se llama a create_checkout_pro
        mp_config_cls.create_checkout_pro.assert_not_called()


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


class TestUserFilterDomain(unittest.TestCase):
    """Tests del paso 9: domain dinámico por user_filter."""

    def test_root_user_sees_everything(self):
        """user_id == 0 (root) → domain vacío."""
        from sale_async_payment.async_payment import AsyncPayment
        result = AsyncPayment._get_user_filter_domain(
            user_filter=MagicMock(),
            is_supervisor=False, user_id=0)
        self.assertEqual(result, [])

    def test_supervisor_sees_everything(self):
        """Supervisor → domain vacío independiente del filtro."""
        from sale_async_payment.async_payment import AsyncPayment
        result = AsyncPayment._get_user_filter_domain(
            user_filter=MagicMock(),
            is_supervisor=True, user_id=5)
        self.assertEqual(result, [])

    def test_no_filter_configured_sees_everything(self):
        """Sin user_filter configurado → domain vacío."""
        from sale_async_payment.async_payment import AsyncPayment
        result = AsyncPayment._get_user_filter_domain(
            user_filter=None, is_supervisor=False, user_id=5)
        self.assertEqual(result, [])

    def test_shops_filter_limits_to_those_shops(self):
        """user_filter.shops != [] → domain limita a esas sucursales."""
        from sale_async_payment.async_payment import AsyncPayment
        uf = MagicMock()
        uf.shops = [MagicMock(id=1), MagicMock(id=3)]
        uf.only_own = False
        result = AsyncPayment._get_user_filter_domain(
            user_filter=uf, is_supervisor=False, user_id=5)
        self.assertEqual(result, [('shop', 'in', [1, 3])])

    def test_only_own_filter_adds_create_uid(self):
        """user_filter.only_own=True → agrega ('create_uid', '=', user_id)."""
        from sale_async_payment.async_payment import AsyncPayment
        uf = MagicMock()
        uf.shops = []
        uf.only_own = True
        result = AsyncPayment._get_user_filter_domain(
            user_filter=uf, is_supervisor=False, user_id=5)
        self.assertEqual(result, [('create_uid', '=', 5)])

    def test_shops_and_only_own_combined(self):
        """Ambos filtros activos → ambos en el domain."""
        from sale_async_payment.async_payment import AsyncPayment
        uf = MagicMock()
        uf.shops = [MagicMock(id=2)]
        uf.only_own = True
        result = AsyncPayment._get_user_filter_domain(
            user_filter=uf, is_supervisor=False, user_id=7)
        self.assertEqual(result, [
            ('shop', 'in', [2]),
            ('create_uid', '=', 7),
        ])


class TestUnassignedPayment(unittest.TestCase):
    """Tests del paso 10: modelo virtual Pagos no asignados."""

    def test_resolve_source_mp(self):
        """ID en rango MP → ('mp', source_id)."""
        from sale_async_payment.unassigned_payment import (
            UnassignedPayment, _MP_OFFSET)
        source, sid = UnassignedPayment._resolve_source(
            _MP_OFFSET + 42)
        self.assertEqual(source, 'mp')
        self.assertEqual(sid, 42)

    def test_resolve_source_qr(self):
        """ID en rango QR → ('qr', source_id)."""
        from sale_async_payment.unassigned_payment import (
            UnassignedPayment, _QR_OFFSET)
        source, sid = UnassignedPayment._resolve_source(
            _QR_OFFSET + 17)
        self.assertEqual(source, 'qr')
        self.assertEqual(sid, 17)

    def test_build_async_vals_for_mp_source(self):
        """Vals para MP: payment_method=mp_link, mp_transaction seteado."""
        from sale_async_payment.unassigned_payment import UnassignedPayment

        sale_record = MagicMock(id=99)
        unassigned = MagicMock(
            source='mp', source_id=7, amount=Decimal('1000'),
            reference='MP-PAY-XYZ', payer_name='comprador@mail.com',
            payer_cuit='', statement_line=MagicMock(id=200))

        # Mock _get_source_record para evitar Pool real
        source_record = MagicMock()
        source_record.config.journal.id = 5
        now = datetime.datetime(2026, 5, 21, 10, 0)

        with patch.object(
                UnassignedPayment, '_get_source_record',
                return_value=source_record), \
             patch(
                'sale_async_payment.unassigned_payment.Transaction'
             ) as TxnMock:
            TxnMock.return_value.user = 3
            vals = UnassignedPayment._build_async_vals(
                unassigned, sale_record, now)

        self.assertEqual(vals['sale'], 99)
        self.assertEqual(vals['amount'], Decimal('1000'))
        self.assertEqual(vals['received_amount'], Decimal('1000'))
        self.assertEqual(vals['journal'], 5)
        self.assertEqual(vals['payment_method'], 'mp_link')
        self.assertEqual(vals['state'], 'confirmed')
        self.assertEqual(vals['statement_line'], 200)
        self.assertEqual(vals['mp_transaction'], 7)
        self.assertEqual(vals['mp_payment_id'], 'MP-PAY-XYZ')
        self.assertEqual(vals['match_criteria'], 'manual')
        self.assertEqual(vals['confirmed_by'], 3)
        self.assertNotIn('qr_detection', vals)

    def test_build_async_vals_for_qr_source(self):
        """Vals para QR: payment_method=bank_transfer, qr_detection seteado."""
        from sale_async_payment.unassigned_payment import UnassignedPayment

        sale_record = MagicMock(id=44)
        unassigned = MagicMock(
            source='qr', source_id=15, amount=Decimal('800'),
            reference='BANK-REF-999', payer_name='Juan Pérez',
            payer_cuit='20-12345678-9', statement_line=None)

        source_record = MagicMock()
        source_record.config.journal.id = 11
        now = datetime.datetime(2026, 5, 21, 11, 0)

        with patch.object(
                UnassignedPayment, '_get_source_record',
                return_value=source_record), \
             patch(
                'sale_async_payment.unassigned_payment.Transaction'
             ) as TxnMock:
            TxnMock.return_value.user = 8
            vals = UnassignedPayment._build_async_vals(
                unassigned, sale_record, now)

        self.assertEqual(vals['payment_method'], 'bank_transfer')
        self.assertEqual(vals['qr_detection'], 15)
        self.assertEqual(vals['bank_reference'], 'BANK-REF-999')
        self.assertIsNone(vals['statement_line'])
        self.assertNotIn('mp_transaction', vals)


class TestFindCandidateHelpers(unittest.TestCase):
    """Tests del paso 11: helpers de find_candidate."""

    def test_normalize_cuit_strips_separators(self):
        from sale_async_payment.async_payment import AsyncPayment
        self.assertEqual(
            AsyncPayment._normalize_cuit('20-12345678-9'), '20123456789')
        self.assertEqual(
            AsyncPayment._normalize_cuit('20.12345678.9'), '20123456789')
        self.assertEqual(
            AsyncPayment._normalize_cuit('20 12345678 9'), '20123456789')

    def test_normalize_cuit_empty(self):
        from sale_async_payment.async_payment import AsyncPayment
        self.assertEqual(AsyncPayment._normalize_cuit(None), '')
        self.assertEqual(AsyncPayment._normalize_cuit(''), '')
        self.assertEqual(AsyncPayment._normalize_cuit('abc'), '')

    def test_build_match_domains_orders_by_specificity(self):
        from sale_async_payment.async_payment import AsyncPayment
        payment_data = {
            'mp_payment_id': 'MP-001',
            'bank_reference': 'BANK-9',
            'payer_cuit': '20-12345678-9',
        }
        domains = AsyncPayment._build_match_domains(
            payment_data, Decimal('500'))
        criterias = [c for _, c in domains]
        self.assertEqual(
            criterias,
            ['mp_payment_id', 'bank_reference',
             'payer_cuit', 'amount_exact'])

    def test_build_match_domains_empty_payload(self):
        from sale_async_payment.async_payment import AsyncPayment
        self.assertEqual(
            AsyncPayment._build_match_domains({}, None), [])

    def test_build_match_domains_only_amount(self):
        from sale_async_payment.async_payment import AsyncPayment
        domains = AsyncPayment._build_match_domains({}, Decimal('100'))
        self.assertEqual(len(domains), 1)
        self.assertEqual(domains[0][1], 'amount_exact')
        self.assertIn(('amount', '=', Decimal('100')), domains[0][0])


class TestFindCandidate(unittest.TestCase):
    """Tests del paso 11: find_candidate."""

    def test_find_candidate_no_data_returns_no_data(self):
        from sale_async_payment.async_payment import AsyncPayment
        result = AsyncPayment.find_candidate({})
        self.assertFalse(result['matched'])
        self.assertEqual(result['reason'], 'no_data')

    def test_find_candidate_invalid_payload(self):
        from sale_async_payment.async_payment import AsyncPayment
        result = AsyncPayment.find_candidate('not-a-dict')
        self.assertFalse(result['matched'])
        self.assertEqual(result['reason'], 'invalid_payload')

    def test_find_candidate_unique_match_writes_suggested(self):
        """Match único → write con state='suggested' + datos del payer."""
        from sale_async_payment.async_payment import AsyncPayment

        candidate = MagicMock(id=42, state='pending', amount=Decimal('1000'))

        with patch.object(
                AsyncPayment, 'search', return_value=[candidate]
                ) as search_mock, \
             patch.object(AsyncPayment, '_lock_for_update') as lock_mock, \
             patch.object(AsyncPayment, 'write') as write_mock:
            result = AsyncPayment.find_candidate({
                'bank_reference': 'BANK-REF-123',
                'amount': '1000',
                'payer_name': 'Juan',
                'payer_cuit': '20-12345678-9',
            })

        self.assertTrue(result['matched'])
        self.assertEqual(result['async_id'], 42)
        self.assertEqual(result['match_criteria'], 'bank_reference')

        lock_mock.assert_called_once_with([42])
        write_mock.assert_called_once()
        args, _ = write_mock.call_args
        self.assertEqual(args[0], [candidate])
        vals = args[1]
        self.assertEqual(vals['state'], 'suggested')
        self.assertEqual(vals['match_criteria'], 'bank_reference')
        self.assertEqual(vals['received_amount'], Decimal('1000'))
        self.assertEqual(vals['bank_reference'], 'BANK-REF-123')
        self.assertEqual(vals['payer_name'], 'Juan')

    def test_find_candidate_ambiguous_does_not_write(self):
        """Múltiples candidatos sin desempate → no write, reason='ambiguous'."""
        from sale_async_payment.async_payment import AsyncPayment

        m1 = MagicMock(id=1, state='pending', amount=Decimal('500'))
        m2 = MagicMock(id=2, state='pending', amount=Decimal('700'))

        with patch.object(
                AsyncPayment, 'search', return_value=[m1, m2]), \
             patch.object(AsyncPayment, '_lock_for_update') as lock_mock, \
             patch.object(AsyncPayment, 'write') as write_mock:
            # amount=300 no coincide con ninguno → ambigüedad sigue
            result = AsyncPayment.find_candidate({
                'payer_cuit': '20-12345678-9',
                'amount': '300',
            })

        self.assertFalse(result['matched'])
        self.assertEqual(result['reason'], 'ambiguous')
        self.assertEqual(result['candidate_count'], 2)
        lock_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_find_candidate_ambiguous_resolved_by_amount(self):
        """2 candidatos por CUIT pero solo uno con amount exacto → match."""
        from sale_async_payment.async_payment import AsyncPayment

        m1 = MagicMock(id=1, state='pending', amount=Decimal('500'))
        m2 = MagicMock(id=2, state='pending', amount=Decimal('800'))

        with patch.object(
                AsyncPayment, 'search', return_value=[m1, m2]), \
             patch.object(AsyncPayment, '_lock_for_update'), \
             patch.object(AsyncPayment, 'write') as write_mock:
            result = AsyncPayment.find_candidate({
                'payer_cuit': '20-12345678-9',
                'amount': '800',
            })

        self.assertTrue(result['matched'])
        self.assertEqual(result['async_id'], 2)
        self.assertEqual(result['match_criteria'], 'payer_cuit')
        write_mock.assert_called_once()

    def test_find_candidate_no_match_returns_no_candidate(self):
        """search retorna [] en todos los tiers → no_candidate."""
        from sale_async_payment.async_payment import AsyncPayment

        with patch.object(AsyncPayment, 'search', return_value=[]), \
             patch.object(AsyncPayment, '_lock_for_update') as lock_mock:
            result = AsyncPayment.find_candidate({
                'mp_payment_id': 'MP-X',
                'amount': '500',
            })

        self.assertFalse(result['matched'])
        self.assertEqual(result['reason'], 'no_candidate')
        lock_mock.assert_not_called()


class TestExpireCron(unittest.TestCase):
    """Tests del paso 12: cron de expiración."""

    def test_expire_domain_filters_correctly(self):
        """Domain incluye pending+suggested, exige expiration_date no null
        y <= now. expired/confirmed/cancelled quedan fuera."""
        from sale_async_payment.async_payment import AsyncPayment
        now = datetime.datetime(2026, 5, 21, 12, 0)
        domain = AsyncPayment._expire_domain(now)
        self.assertIn(('state', 'in', ['pending', 'suggested']), domain)
        self.assertIn(('expiration_date', '!=', None), domain)
        self.assertIn(('expiration_date', '<=', now), domain)

    def test_expire_cron_writes_expired_when_candidates_found(self):
        """expire_cron busca con _expire_domain y hace write a 'expired'."""
        from sale_async_payment.async_payment import AsyncPayment
        a1 = MagicMock(id=1)
        a2 = MagicMock(id=2)
        with patch.object(
                AsyncPayment, 'search', return_value=[a1, a2]
                ) as search_mock, \
             patch.object(AsyncPayment, 'write') as write_mock:
            AsyncPayment.expire_cron()
        search_mock.assert_called_once()
        write_mock.assert_called_once_with([a1, a2], {'state': 'expired'})

    def test_expire_cron_noop_when_nothing_to_expire(self):
        """Sin candidatos → no write."""
        from sale_async_payment.async_payment import AsyncPayment
        with patch.object(AsyncPayment, 'search', return_value=[]), \
             patch.object(AsyncPayment, 'write') as write_mock:
            AsyncPayment.expire_cron()
        write_mock.assert_not_called()


class TestIntegralFlows(unittest.TestCase):
    """Tests integrales del paso 13: flows que combinan varios componentes."""

    def test_trigger_workflow_to_end_when_sale_fully_paid(self):
        """_maybe_trigger_workflow_to_end llama Sale.workflow_to_end cuando
        la venta queda totalmente cobrada (total == paid) y está en un
        estado avanzable."""
        from sale_async_payment.async_payment import AsyncPayment

        refreshed_sale = MagicMock(
            total_amount=Decimal('1000'),
            paid_amount=Decimal('1000'),
            state='processing')

        sale_cls = MagicMock()
        sale_cls.browse.return_value = [refreshed_sale]
        pool_mock = MagicMock()
        pool_mock.get.return_value = sale_cls

        with patch(
                'sale_async_payment.async_payment.Pool',
                return_value=pool_mock):
            AsyncPayment._maybe_trigger_workflow_to_end({99})

        sale_cls.workflow_to_end.assert_called_once_with(
            [refreshed_sale])

    def test_no_workflow_trigger_when_residual_remains(self):
        """No dispara workflow_to_end cuando sale.paid_amount < total."""
        from sale_async_payment.async_payment import AsyncPayment

        partial = MagicMock(
            total_amount=Decimal('1000'),
            paid_amount=Decimal('400'),
            state='processing')

        sale_cls = MagicMock()
        sale_cls.browse.return_value = [partial]
        pool_mock = MagicMock()
        pool_mock.get.return_value = sale_cls

        with patch(
                'sale_async_payment.async_payment.Pool',
                return_value=pool_mock):
            AsyncPayment._maybe_trigger_workflow_to_end({99})

        sale_cls.workflow_to_end.assert_not_called()

    def test_no_workflow_trigger_when_sale_already_done(self):
        """No dispara workflow_to_end si la sale ya está en done/cancelled
        (estados terminales fuera del set de avance)."""
        from sale_async_payment.async_payment import AsyncPayment

        done_sale = MagicMock(
            total_amount=Decimal('1000'),
            paid_amount=Decimal('1000'),
            state='done')

        sale_cls = MagicMock()
        sale_cls.browse.return_value = [done_sale]
        pool_mock = MagicMock()
        pool_mock.get.return_value = sale_cls

        with patch(
                'sale_async_payment.async_payment.Pool',
                return_value=pool_mock):
            AsyncPayment._maybe_trigger_workflow_to_end({99})

        sale_cls.workflow_to_end.assert_not_called()

    def test_link_wizard_records_unmatched_difference(self):
        """transition_link_ del wizard de huérfanos setea unmatched_difference
        en la statement.line cuando recibido difiere del total de la venta."""
        from sale_async_payment.unassigned_payment import (
            LinkUnassignedPayment, UnassignedPayment)

        # Unassigned: pago de $1050. La venta vale $1000 → diff +50.
        line = MagicMock(id=300, sale=None)
        unassigned = MagicMock(
            id=1, source='mp', source_id=7,
            amount=Decimal('1050'), reference='MP-XYZ',
            payer_name='comprador@mail.com', payer_cuit='',
            statement_line=line)
        sale = MagicMock(id=99, total_amount=Decimal('1000'))

        async_payment_cls = MagicMock()
        async_payment_cls.create.return_value = [MagicMock(id=11)]
        stmt_line_cls = MagicMock()
        stmt_line_cls.return_value = line

        def pool_get(model):
            return {
                'sale.unassigned_payment': UnassignedPayment,
                'sale.async_payment': async_payment_cls,
                'account.statement.line': stmt_line_cls,
            }[model]
        pool_mock = MagicMock()
        pool_mock.get.side_effect = pool_get

        wizard = MagicMock(spec=LinkUnassignedPayment)
        wizard.record = unassigned
        wizard.start = MagicMock(sale=sale)

        vals_stub = {
            'sale': 99, 'amount': Decimal('1050'),
            'journal': 5, 'state': 'confirmed',
            'payment_method': 'mp_link',
        }

        with patch(
                'sale_async_payment.unassigned_payment.Pool',
                return_value=pool_mock), \
             patch.object(
                UnassignedPayment, '_build_async_vals',
                return_value=vals_stub), \
             patch.object(
                UnassignedPayment, '_set_source_sale') as set_sale_mock:
            LinkUnassignedPayment.transition_link_(wizard)

        # Async creado con los vals
        async_payment_cls.create.assert_called_once_with([vals_stub])
        # _set_source_sale invocado con source/source_id/sale.id
        set_sale_mock.assert_called_once_with('mp', 7, 99)
        # Statement.line: write con sale=99 Y unmatched_difference=50
        stmt_line_cls.write.assert_called_once()
        call_args = stmt_line_cls.write.call_args[0]
        self.assertEqual(call_args[0], [line])
        vals = call_args[1]
        self.assertEqual(vals.get('sale'), 99)
        self.assertEqual(vals.get('unmatched_difference'), Decimal('50'))

    def test_link_wizard_no_diff_when_amounts_match(self):
        """Si el monto recibido == total de la venta, no se setea
        unmatched_difference en la línea."""
        from sale_async_payment.unassigned_payment import (
            LinkUnassignedPayment, UnassignedPayment)

        line = MagicMock(id=300, sale=None)
        unassigned = MagicMock(
            id=1, source='qr', source_id=15,
            amount=Decimal('1000'), reference='REF-1',
            payer_name='Juan', payer_cuit='20-12345678-9',
            statement_line=line)
        sale = MagicMock(id=99, total_amount=Decimal('1000'))

        async_payment_cls = MagicMock()
        async_payment_cls.create.return_value = [MagicMock(id=11)]
        stmt_line_cls = MagicMock()
        stmt_line_cls.return_value = line  # StatementLine(id) → line

        def pool_get(model):
            return {
                'sale.unassigned_payment': UnassignedPayment,
                'sale.async_payment': async_payment_cls,
                'account.statement.line': stmt_line_cls,
            }[model]
        pool_mock = MagicMock()
        pool_mock.get.side_effect = pool_get

        wizard = MagicMock(spec=LinkUnassignedPayment)
        wizard.record = unassigned
        wizard.start = MagicMock(sale=sale)

        with patch(
                'sale_async_payment.unassigned_payment.Pool',
                return_value=pool_mock), \
             patch.object(
                UnassignedPayment, '_build_async_vals',
                return_value={'sale': 99, 'journal': 5}), \
             patch.object(UnassignedPayment, '_set_source_sale'):
            LinkUnassignedPayment.transition_link_(wizard)

        stmt_line_cls.write.assert_called_once()
        vals = stmt_line_cls.write.call_args[0][1]
        self.assertEqual(vals.get('sale'), 99)
        self.assertNotIn('unmatched_difference', vals)

    def test_effective_residual_zero_blocks_extra_sync_payment(self):
        """Cuando paid + async_pending iguala el total, effective_residual
        queda en 0 → debería bloquear cobro sync adicional. Verificamos el
        invariante directo sobre get_effective_residual_amount."""
        from sale_async_payment.sale import Sale
        sale = MagicMock(id=1)
        sale.total_amount = Decimal('1000')
        sale.paid_amount = Decimal('300')
        sale.async_pending_amount = Decimal('700')
        residual = Sale.get_effective_residual_amount(
            [sale], 'effective_residual_amount')
        self.assertEqual(residual[1], Decimal('0'))


if __name__ == '__main__':
    unittest.main()
