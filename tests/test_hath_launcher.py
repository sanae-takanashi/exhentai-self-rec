import socket
import tempfile
import unittest
from pathlib import Path

from scripts.run_with_hath import build_ssh_command, configure_pack_environment, ensure_local_port_available, read_token


class HathLauncherTest(unittest.TestCase):
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
