import io,json,socket,ssl,unittest
from urllib.error import HTTPError,URLError
from unittest.mock import patch
from k1monitor.notifications import TelegramError,telegram_request,telegram_ssl_context

class TelegramTransportTests(unittest.TestCase):
    def test_bundled_roots_work_with_empty_system_store_and_keep_verification(self):
        empty=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch('k1monitor.notifications.ssl.create_default_context',return_value=empty):context=telegram_ssl_context()
        self.assertIs(context,empty);self.assertGreater(context.cert_store_stats()['x509_ca'],100)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)

    def test_network_causes_are_distinct_and_never_echo_url_or_token(self):
        for error,expected in [(ssl.SSLCertVerificationError(1,'secret URL'),'сертификат'),(socket.gaierror(11001,'secret URL'),'DNS'),(TimeoutError('secret URL'),'12 секунд'),(ConnectionResetError('secret URL'),'прервано')]:
            with patch('k1monitor.notifications.build_opener') as factory:
                factory.return_value.open.side_effect=URLError(error)
                with self.assertRaises(TelegramError) as caught:telegram_request('SENSITIVE','getMe',{})
            self.assertIn(expected,str(caught.exception));self.assertNotIn('secret',str(caught.exception));self.assertNotIn('SENSITIVE',str(caught.exception))

    def test_invalid_api_token_remains_distinct_from_network_errors(self):
        with patch('k1monitor.notifications.build_opener') as factory:
            factory.return_value.open.side_effect=HTTPError('https://api.telegram.org/botSENSITIVE/getMe',401,'secret',{},io.BytesIO(b'{}'))
            with self.assertRaises(TelegramError) as caught:telegram_request('SENSITIVE','getMe',{})
        self.assertIn('отклонил токен',str(caught.exception));self.assertNotIn('SENSITIVE',str(caught.exception))

    def test_invalid_json_is_not_reported_as_missing_internet(self):
        with patch('k1monitor.notifications.build_opener') as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value=b'<html>vpn</html>'
            with self.assertRaisesRegex(TelegramError,'некорректный ответ'):telegram_request('fixture','getMe',{})

    def test_malformed_success_response_is_handled(self):
        with patch('k1monitor.notifications.build_opener') as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value=b'{"ok":true}'
            with self.assertRaisesRegex(TelegramError,'не подтвердил'):telegram_request('fixture','getMe',{})
