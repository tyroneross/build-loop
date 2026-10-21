"""Synthetic credential-policy tests: never run a credential command."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from credential_policy import command_reason
import privileged_broker as broker
from test_system_access_request import args
import system_access_request as access

ROOT = Path(__file__).resolve().parents[1]


class CredentialPolicyTests(unittest.TestCase):
    def reason(self, command, **kwargs):
        return command_reason(command, platform='darwin', environment={}, helpers=[], **kwargs)

    def test_denies_known_access_forms_without_execution(self):
        commands = [
            'security find-generic-password -w',
            '/usr/bin/security dump-keychain',
            'env MODE=test sudo /usr/bin/security unlock-keychain',
            'echo ready\nsecurity find-internet-password',
            "bash -c 'security find-generic-password'",
            'git-credential-osxkeychain get',
            "bash -lc 'security find-generic-password'",
            'env -u GH_TOKEN security find-generic-password',
            'echo `security find-generic-password`',
            'echo "$(security find-generic-password)"',
            '2>/dev/null security find-generic-password',

            r'secu\rity find-generic-password',
            'git -c credential.helper= -c credential.https://github.com.helper=osxkeychain push origin main',
            'cd /different/repo && git push origin main',
            'env -C /different/repo git push origin main',
            "cd /different/repo; bash -c 'git push origin main'",
            'cd /different/repo && echo "$(git push origin main)"',


            'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=osxkeychain git push',
            'git --config-env=credential.helper=MY_HELPER push',

            "python3 -c 'SecItemCopyMatching(query, result)'",
            "swift -e 'SecKeychainUnlock()'",
            'python3 privileged_broker.py request --argv security find-generic-password',
            'python3 system_access_request.py -- security find-generic-password',
            'vault get id --value', 'vault get id --value=true', 'vault dump',
            'gh auth status', 'claude -p hello',
        ]
        with patch('credential_policy.subprocess.run', side_effect=AssertionError('executed')):
            for command in commands:
                with self.subTest(command=command):
                    self.assertIsNotNone(self.reason(command))

    def test_metadata_and_literal_mentions_are_allowed(self):
        for command in ["echo 'security find-generic-password'", 'rg keychain docs',
                        "echo '$(security find-generic-password)'", "echo '`security find-generic-password`'",
                        'vault status', 'vault list --search service --json',
                        'vault inject id --project-path /tmp/project --env-key TOKEN --env-file .env --json',
                        'git log --grep push',
                        'codesign --sign - app', 'security help', 'gh --help',
                        "python3 -c 'print(\"SecItemCopyMatching\")'"]:

            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))

    def test_git_helper_reset_and_network_scope(self):
        for helper in ['osxkeychain', '/usr/bin/git-credential-osxkeychain', 'store']:
            self.assertIsNotNone(command_reason('git push origin main', platform='darwin', helpers=[helper]))
            self.assertIsNone(command_reason('git -c credential.helper= fetch origin', platform='darwin', helpers=[helper]))
            self.assertIsNone(command_reason('git log --grep push', platform='darwin', helpers=[helper]))

    def test_approved_environment_route_does_not_read_values(self):
        self.assertIsNotNone(command_reason('gh auth login', platform='darwin', environment={'GH_TOKEN': 'synthetic'}))
        self.assertIsNotNone(command_reason('claude auth login', platform='darwin', environment={'ANTHROPIC_API_KEY': 'synthetic'}))
        self.assertIsNotNone(command_reason('gh api repos/a/b', platform='darwin', environment={'GH_TOKEN': 'synthetic'}))
        self.assertIsNotNone(command_reason('claude -p hello', platform='darwin', environment={'ANTHROPIC_API_KEY': 'synthetic'}))

    def test_command_environment_removal_cannot_fall_back_to_stored_auth(self):
        for command, environment in [
            ('GH_TOKEN=$MISSING gh api repos/a/b', {'GH_TOKEN': 'synthetic'}),
            ('env -uGH_TOKEN gh api repos/a/b', {'GH_TOKEN': 'synthetic'}),
            ('unset GH_TOKEN; gh api repos/a/b', {'GH_TOKEN': 'synthetic'}),
            ('env -u GH_TOKEN -u GITHUB_TOKEN gh api repos/a/b', {'GH_TOKEN': 'synthetic'}),
            ('env -i gh api repos/a/b', {'GH_TOKEN': 'synthetic'}),
            ('GH_TOKEN= gh auth token', {'GH_TOKEN': 'synthetic'}),
            ('env --unset=ANTHROPIC_API_KEY claude -p hello', {'ANTHROPIC_API_KEY': 'synthetic'}),
            ("env -i bash -lc 'gh api repos/a/b'", {'GH_TOKEN': 'synthetic'}),
        ]:
            with self.subTest(command=command):
                self.assertIsNotNone(command_reason(command, platform='darwin', helpers=[], environment=environment))
        self.assertIsNotNone(command_reason('env MODE=test gh api repos/a/b', platform='darwin', helpers=[], environment={'GH_TOKEN': 'synthetic'}))

    def test_system_wrapper_never_dispatches_or_creates_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            runner = unittest.mock.Mock(side_effect=AssertionError('dispatched'))
            for overrides in [dict(command=['security', 'find-generic-password']),
                              dict(command=['fake'], scope='Keychain credentials'),
                              dict(command=['security', 'find-generic-password'],
                                   resume_request='previous', user_confirmation_ref='general-continue')]:
                self.assertEqual(access.run_request(args(state, **overrides), runner), access.HELD)
            runner.assert_not_called()
            self.assertFalse((state / 'ledger.json').exists())

    def test_explicit_keychain_scopes_refuse_every_prefix_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            runner = unittest.mock.Mock(side_effect=AssertionError('dispatched'))
            for scope in ['Apple Keychain credentials', 'Signing / KEYCHAIN certificate', ' keychain access']:
                self.assertEqual(access.run_request(args(state, command=['fake-tool'], scope=scope), runner), access.HELD)
                with patch.object(broker, 'load_registry', side_effect=AssertionError('registry')):
                    with self.assertRaises(ValueError):
                        broker.build_request(argv=['fake-tool'], purpose='synthetic', task_id='test', scope=scope)
            runner.assert_not_called()
            self.assertFalse((state / 'ledger.json').exists())

    def test_broker_refuses_before_registry_or_resolution(self):
        with patch.object(broker, 'load_registry', side_effect=AssertionError('registry')):
            with self.assertRaises(ValueError):
                broker.build_request(argv=['security', 'find-generic-password'], purpose='test', task_id='test')

    def test_dispatcher_denies_even_when_hooks_disabled(self):
        env = dict(os.environ, BUILD_LOOP_HOOKS='off', CLAUDE_PLUGIN_ROOT=str(ROOT))
        for command in ['security find-generic-password -w',
                        "bash -lc 'security find-generic-password'",
                        'env -u GH_TOKEN security find-generic-password',
                        r'secu\rity find-generic-password',
                        'echo `security find-generic-password`',
                        '2>/dev/null security find-generic-password',
                        'echo "$(security find-generic-password)"']:
            event = {'tool_input': {'command': command}, 'cwd': '/tmp'}
            result = subprocess.run(['bash', str(ROOT / 'scripts/hooks/pre_bash_dispatch.sh')],
                                    input=json.dumps(event), text=True, capture_output=True, env=env, timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny', command)

    def test_scoped_helpers_are_not_erased_by_a_global_reset(self):
        result = subprocess.CompletedProcess([], 0, 'credential.https://github.com.helper osxkeychain\ncredential.helper \n', '')
        with patch('credential_policy.subprocess.run', return_value=result) as runner:
            self.assertIsNotNone(command_reason('git -c credential.helper= fetch origin', platform='darwin'))
        self.assertIn('--get-regexp', runner.call_args.args[0])



if __name__ == '__main__':
    unittest.main()
