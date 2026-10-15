import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.run_with_hath import (
    build_ssh_command,
    configure_pack_environment,
    ensure_local_port_available,
    main,
    normalize_ssh_host,
    read_token,
    supervise,
)


class HathLauncherTest(unittest.TestCase):
    def test_normalize_ssh_host_removes_accidental_trailing_backslashes(self):
        for host in ("exh-rec-hath\\", "exh-rec-hath\\\\", "  exh-rec-hath\\  ", " exh-rec-hath "):
            with self.subTest(host=host):
                self.assertEqual(normalize_ssh_host(host), "exh-rec-hath")

    def test_normalize_ssh_host_preserves_valid_destinations(self):
        for host in ("exh-rec-hath", "user@host.example", "192.0.2.1", "user@2001:db8::1"):
            with self.subTest(host=host):
                self.assertEqual(normalize_ssh_host(host), host)

    def test_empty_ssh_host_fails_before_credentials_or_processes(self):
        for host in ("", "  ", "\\", " \\\\ "):
            with self.subTest(host=host), patch("scripts.run_with_hath.read_token") as token_reader, patch(
                "scripts.run_with_hath.subprocess.Popen"
            ) as popen:
                with self.assertRaisesRegex(RuntimeError, "SSH host must not be empty"):
                    supervise(Path("unused-token"), Path("unused-config"), host, 18787, 18788, 5.0, "ssh.exe")
                token_reader.assert_not_called()
                popen.assert_not_called()

    def test_launcher_uses_normalized_host_for_tunnel_and_pack_environment(self):
        receiver = Mock(returncode=0)
        receiver.poll.side_effect = [None, 0]
        tunnel = Mock()
        with patch("scripts.run_with_hath.read_token", return_value="a" * 64), patch(
            "scripts.run_with_hath.Path.is_file", return_value=True
        ), patch("scripts.run_with_hath.ensure_local_port_available"), patch(
            "scripts.run_with_hath.subprocess.Popen", side_effect=[receiver, tunnel]
        ) as popen, patch("scripts.run_with_hath.time.sleep"), patch(
            "scripts.run_with_hath.stop_process"
        ), patch("scripts.run_with_hath.print") as output:
            result = main([
                "--token-file", "unused-token", "--ssh-config", "unused-config",
                "--ssh-host", "exh-rec-hath\\", "--ssh-executable", "ssh.exe",
            ])

        self.assertEqual(result, 0)
        environment = popen.call_args_list[0].kwargs["env"]
        self.assertEqual(environment["EXH_REC_HATH_SSH_HOST"], "exh-rec-hath")
        command = popen.call_args_list[1].args[0]
        self.assertEqual(command[-1], "exh-rec-hath")
        self.assertIn("127.0.0.1:18788:127.0.0.1:18787", command)
        self.assertTrue(any("normalized" in call.args[0] for call in output.call_args_list))

    def test_local_port_check_rejects_an_existing_server(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        try:
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                ensure_local_port_available(listener.getsockname()[1])
        finally:
            listener.close()

    def test_read_token_rejects_short_or_whitespace_values(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token.secret"
            token_file.write_text("too-short\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                read_token(token_file)

            token_file.write_text("x" * 32 + " bad", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                read_token(token_file)

    def test_read_token_returns_stripped_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "token.secret"
            token_file.write_text("a" * 64 + "\n", encoding="utf-8")
            self.assertEqual(read_token(token_file), "a" * 64)

    def test_build_ssh_command_uses_loopback_reverse_forward(self):
        command = build_ssh_command(
            "ssh.exe",
            Path("C:/secrets/hath-ssh-config"),
            "exh-rec-hath",
            local_port=18787,
            remote_port=18788,
        )

        self.assertEqual(command[:3], ["ssh.exe", "-F", "C:/secrets/hath-ssh-config"])
        self.assertIn("127.0.0.1:18788:127.0.0.1:18787", command)
        self.assertEqual(command[-1], "exh-rec-hath")

    def test_configure_pack_environment_exposes_existing_ssh_connection_details(self):
        environment = {}
        configure_pack_environment(
            environment,
            "C:/Windows/System32/OpenSSH/ssh.exe",
            Path("C:/secrets/hath-ssh-config"),
            "exh-rec-hath",
        )

        self.assertEqual(environment["EXH_REC_HATH_SSH_HOST"], "exh-rec-hath")
        self.assertEqual(environment["EXH_REC_HATH_SSH_CONFIG"], "C:/secrets/hath-ssh-config")
        self.assertEqual(environment["EXH_REC_HATH_SSH_EXECUTABLE"], "C:/Windows/System32/OpenSSH/ssh.exe")


if __name__ == "__main__":
    unittest.main()
