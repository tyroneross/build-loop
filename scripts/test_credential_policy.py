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
                        'echo "$(security find-generic-password)"',
                        # 2026-10-25 review: expansion and git shell bodies.
                        "$'security' find-generic-password -w",
                        'sec${EMPTY}urity find-generic-password -w',
                        "git -c credential.helper= submodule foreach 'security find-generic-password -w'"]:
            event = {'tool_input': {'command': command}, 'cwd': '/tmp'}
            result = subprocess.run(['bash', str(ROOT / 'scripts/hooks/pre_bash_dispatch.sh')],
                                    input=json.dumps(event), text=True, capture_output=True, env=env, timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny', command)

    ssh_files = []  # hermetic: no real ssh config unless a test supplies one

    def decide(self, config_lines, command='git fetch origin', environment=None):
        config = ''.join(line + '\n' for line in config_lines)

        values = dict(line.partition(' ')[::2] for line in config_lines)

        def fake_git(argv, **kwargs):
            # Only metadata reads happen: the context config read, alias
            # lookups, and the superproject lookup (answered "no work tree").
            if '--get-regexp' in argv:
                # Like git -z: "key\nvalue\0" records; `-c k=v` values come last.
                records = [line.partition(' ')[::2] for line in config_lines]
                records += [tuple(argv[i + 1].split('=', 1)) for i, arg in enumerate(argv[:-1]) if arg == '-c']
                out = ''.join(f'{key}\n{value}\0' for key, value in records)
                return subprocess.CompletedProcess(argv, 0, out, '')
            if '--get' in argv:
                key = argv[argv.index('--get') + 1]
                found = key in values
                return subprocess.CompletedProcess(argv, 0 if found else 1, values.get(key, '') + '\n', '')
            return subprocess.CompletedProcess(argv, 1, '', '')

        with patch('credential_policy.subprocess.run', side_effect=fake_git) as runner, \
                patch('credential_policy._ssh_config_files', return_value=self.ssh_files, create=True):
            reason = command_reason(command, platform='darwin', environment=environment or {})
        if reason is None:  # an allow must come from actually judging the config
            self.assertTrue(any('--get-regexp' in call.args[0] for call in runner.call_args_list))
        return reason

    # Git keeps ONE helper list per URL in config order; an empty value clears
    # everything before it. Verified 2026-10-23 with git 2.54 and logging fake
    # helpers (`git credential fill`): system osxkeychain + global
    # `credential.https://github.com.helper=` + gh => only gh runs for github.com.
    GH_SETUP = ['credential.helper osxkeychain',
                'credential.https://github.com.helper ',
                'credential.https://github.com.helper !/opt/homebrew/bin/gh auth git-credential']

    def test_gh_setup_git_overrides_system_keychain_for_github(self):
        self.assertIsNone(self.decide(self.GH_SETUP + ['remote.origin.url https://github.com/o/r.git']))
        self.assertIsNone(self.decide(self.GH_SETUP + ['remote.origin.url https://github.com/o/r.git'],
                                      'git push origin main'))

    def test_keychain_still_applies_to_other_hosts_and_unknown_urls(self):
        self.assertIsNotNone(self.decide(self.GH_SETUP + ['remote.origin.url https://gitlab.com/o/r.git']))
        self.assertIsNotNone(self.decide(self.GH_SETUP))  # no remote: URL unknown
        two_remotes = self.GH_SETUP + ['remote.origin.url https://github.com/o/r.git',
                                       'remote.mirror.url https://example.com/r.git']
        self.assertIsNone(self.decide(two_remotes, 'git fetch origin'))   # only origin's context
        self.assertIsNotNone(self.decide(two_remotes, 'git fetch mirror'))
        self.assertIsNotNone(self.decide(two_remotes, 'git fetch'))       # remote unnamed: all
        self.assertIsNotNone(self.decide(self.GH_SETUP, 'git clone https://example.com/r.git'))
        # Clone from github.com joined the gh helper approval (owner, 2026-10-25).
        self.assertIsNone(self.decide(self.GH_SETUP, 'git clone https://github.com/o/r.git'))
        self.assertIsNone(self.decide(self.GH_SETUP, 'git -c credential.helper= clone https://github.com/o/r.git'))

    # Shapes from the independent Codex review of 9d7f68f3 (2026-10-23): each
    # was allowed there while git would still run osxkeychain.
    def test_review_shapes_that_still_reach_the_keychain_are_refused(self):
        gh = self.GH_SETUP + ['remote.origin.url https://github.com/o/r.git']
        cases = [
            (self.GH_SETUP + ['remote.origin.url https://github.com:8443/o/r.git'], 'git fetch origin', None),
            (['credential.helper osxkeychain', 'credential.https://git*.com.helper ',
              'remote.origin.url https://github.com/o/r.git'], 'git fetch origin', None),
            (self.GH_SETUP + ['remote.origin.url alias:.evil.example/o/r.git',
                              'url.https://github.com.insteadof alias:'], 'git fetch origin', None),
            (gh, 'git fetch "$TARGET_URL"', None),
            (gh, 'git credential fill', None),
            (self.GH_SETUP, 'git clone --config credential.helper=osxkeychain https://github.com/o/r.git', None),
            (self.GH_SETUP, 'git clone --recurse-submodules https://github.com/o/r.git', None),
            (gh, 'git -c http.proxy=http://alice@proxy.example:8080 fetch origin', None),
            (gh, 'git fetch origin', {'HTTPS_PROXY': 'http://alice@proxy.example:8080'}),
            (['credential.helper osxkeychain'], 'git fetch https::https://gitlab.com/o/r.git', None),
            (gh + ['submodule.lib.url https://gitlab.com/o/lib.git'], 'git pull', None),
        ]
        for config, command, environment in cases:
            with self.subTest(command=command, config=config[-1]):
                self.assertIsNotNone(self.decide(config, command, environment))
        # Second review (2026-10-23) of 9cb969cf.
        round2 = [
            (['credential.helper !gh auth git-credential'],
             'git clone --config credential.helper=osxkeychain https://github.com/o/r.git'),
            (['credential.helper !gh auth git-credential'],
             'git clone -c credential.helper=osxkeychain https://github.com/o/r.git'),
            (gh, 'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper '
                 'GIT_CONFIG_VALUE_0=osxkeychain; git fetch origin'),
            (gh, 'HOME=/tmp/other git fetch origin'),
            (gh, 'git -c credential.helper=osxkeychain remote update'),
            (['credential.helper osxkeychain', 'remote.origin.url https://gitlab.com/o/r.git'],
             'git submodule update --remote'),
            (['credential.helper !git credential-store', 'remote.origin.url https://github.com/o/r.git'],
             'git fetch origin'),
            (['credential.helper /usr/libexec/git-core/git-credential-store',
              'remote.origin.url https://github.com/o/r.git'], 'git fetch origin'),
            (['credential.helper osxkeychain', 'remote.origin.url https://gitlab.com/o/r.git'],
             'git \\\nfetch origin'),
            (gh + ['alias.f !security find-generic-password'], 'git f'),
            (gh + ['alias.sync fetch --all'], 'git sync'),
            (['credential.helper osxkeychain', 'remote.origin.url https://gitlab.com/o/r.git'],
             'git lfs pull'),
            (gh + ['remote.origin.pushurl https://gitlab.com/o/r.git'], 'git push origin main'),
        ]
        for config, command in round2:
            with self.subTest(command=command, config=config[-1]):
                self.assertIsNotNone(self.decide(config, command))
        # Final review (round 3): shapes the ORIGINAL policy refused that the
        # rewritten one briefly allowed.
        round3 = [
            (self.GH_SETUP[:2] + ['credential.https://github.com.helper !security find-generic-password -w; :',
                                  'remote.origin.url https://github.com/o/r.git'], 'git fetch origin'),
            (gh, "git config --local --add credential.helper ''; "
                 "git config --local --add credential.helper osxkeychain; git fetch origin"),
            (gh, 'git remote set-url origin https://gitlab.com/o/r.git && git fetch origin'),
            (gh, "printf '[credential]\\n\\thelper = osxkeychain\\n' >> .git/config; git fetch origin"),
        ]
        for config, command in round3:
            with self.subTest(command=command, config=config[-2]):
                self.assertIsNotNone(self.decide(config, command))
        # A value containing newlines stays ONE value (git config -z): it cannot
        # forge a scoped reset record.
        forged = ['credential.helper osxkeychain get\ncredential.https://github.com.helper \n'
                  'credential.https://github.com.helper !gh auth git-credential',
                  'remote.origin.url https://github.com/o/r.git']
        self.assertIsNotNone(self.decide(forged, 'git fetch origin'))
        # A plain read of git config in the same command changes nothing.
        self.assertIsNone(self.decide(gh, 'git config --get remote.origin.url && git fetch origin'))
        # fetch uses url, not pushurl: an unused push destination is not consulted.
        self.assertIsNone(self.decide(gh + ['remote.origin.pushurl https://gitlab.com/o/r.git'],
                                      'git fetch origin'))
        # The explicit default port is the same context as none.
        self.assertIsNone(self.decide(self.GH_SETUP + ['remote.origin.url https://github.com:443/o/r.git']))
        self.assertIsNone(self.decide(gh + ['submodule.lib.url https://gitlab.com/o/lib.git'],
                                      'git fetch --no-recurse-submodules origin'))

    def test_a_later_unscoped_reset_clears_earlier_scoped_helpers(self):
        self.assertIsNone(self.decide(['credential.https://github.com.helper osxkeychain', 'credential.helper ',
                                       'remote.origin.url https://github.com/o/r.git']))

    def test_scoped_keychain_and_uncertain_scopes_refuse(self):
        remote = 'remote.origin.url https://api.github.com/o/r.git'
        self.assertIsNotNone(self.decide(['credential.https://*.github.com.helper osxkeychain', remote]))
        self.assertIsNotNone(self.decide(['credential.helper osxkeychain',
                                          'credential.https://api.github.com/o.helper ', remote]))
        self.assertIsNotNone(self.decide(['credential.helper store', remote]))

    def test_insteadof_rewrite_targets_are_judged(self):
        self.assertIsNotNone(self.decide(['credential.https://github.com.helper osxkeychain',
                                          'remote.origin.url git@github.com:o/r.git',
                                          'url.https://github.com/.insteadof git@github.com:']))
        self.assertIsNone(self.decide(['credential.helper osxkeychain', 'remote.origin.url git@github.com:o/r.git']))

    def test_local_git_commands_read_no_config(self):
        with patch('credential_policy.subprocess.run', side_effect=AssertionError('read config')):
            for command in ['git status', 'git log --oneline -3', 'git remote -v',
                            'git remote add up https://gitlab.com/o/r.git', 'git submodule status',
                            'git -C /tmp diff', 'git worktree list']:
                with self.subTest(command=command):
                    self.assertIsNone(command_reason(command, platform='darwin', environment={}))

    def test_shell_shapes_that_run_a_command_are_inspected(self):
        for command in ['echo "$(printf \')\'; security find-generic-password)"',
                        "cat <<'EO'F\ntext\nEOF\nsecurity find-generic-password",
                        "printf '%s\\n' 'security find-generic-password' | sh",
                        "echo security find-generic-password | bash -s",
                        "env -S 'security find-generic-password'",
                        "env --split-string='security find-generic-password'",
                        'if true; then security find-generic-password; fi',
                        'while true; do security find-generic-password; done',
                        "eval 'security find-generic-password'",
                        "bash <<< 'security find-generic-password'"]:
            with self.subTest(command=command):
                self.assertIsNotNone(self.reason(command))
        for command in ["printf '%s\\n' 'hello' | sh", 'if true; then echo ok; fi',
                        "cat <<'EO'F\nsecurity find-generic-password\nEOF"]:
            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))

    def test_heredoc_bodies_are_data_unless_a_shell_or_interpreter_reads_them(self):
        for command in ["cat > notes.md <<'EOF'\n`git fetch` was blocked; security find-generic-password\nEOF",
                        "python3 - <<'EOF'\nprint('keyring.get_password is mentioned only')\nEOF\necho done",
                        'cat <<-EOF\n\tgit push origin main\n\tEOF',
                        "cd /tmp && python3 - <<'EOF'\nsecurity = 'not a command'\nprint(security)\nEOF"]:
            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))
        for command in ["bash <<'EOF'\nsecurity find-generic-password\nEOF",
                        "python3 - <<'EOF'\nimport keyring\nkeyring.get_password('s', 'u')\nEOF",
                        'cat <<EOF\n$(security find-generic-password)\nEOF',
                        "cat <<'EOF'\ntext\nEOF\nsecurity find-generic-password",
                        "cat <<'EOF' | sh\nsecurity find-generic-password\nEOF",
                        'eval "$(cat <<\'EOF\'\nsecurity find-generic-password\nEOF\n)"',
                        "source /dev/stdin <<'EOF'\nsecurity find-generic-password\nEOF",
                        "xargs -I{} sh -c {} <<'EOF'\nsecurity find-generic-password\nEOF",
                        "cd /tmp && bash -s <<'EOF'\nsecurity find-generic-password\nEOF",
                        "# <<EOF\nsecurity find-generic-password\nEOF",
                        "echo hi # trailing comment\nsecurity find-generic-password",
                        'echo "<<EOF"\nsecurity find-generic-password\nEOF',
                        "cat <<EOF\n'$(security find-generic-password)'\nEOF",
                        "python3 - <<EOF\nprint('$(security find-generic-password)')\nEOF"]:
            with self.subTest(command=command):
                self.assertIsNotNone(self.reason(command))

    def test_heredoc_is_owned_by_its_pipeline_not_sibling_commands(self):
        body = "import json\nprint(json.loads('{\"groups\": []}')['groups'])\n"
        for separator in [';', '&&', '||']:
            command = f"sed -n '1p' /dev/null {separator} python3 - <<'PY'\n{body}PY"
            self.assertIsNone(self.reason(command), command)
        self.assertIsNone(self.reason("python3 - <<'PY'; sed -n '1p' /dev/null\n" + body + 'PY'))
        for command in [
            "sed -n '1p' /dev/null; bash <<'PY'\nsecurity find-generic-password\nPY",
            "sed -n '1p' /dev/null; cat <<'PY' | sh\nsecurity find-generic-password\nPY",
            "sed -n '1p' /dev/null; python3 - <<'PY'\nimport keyring\nkeyring.get_password('s','u')\nPY",
            "sed -n '1p' /dev/null; eval \"$(cat <<'PY'\nsecurity find-generic-password\nPY\n)\"",
            "sed -n '1p' /dev/null; cat <<PY\n$(security find-generic-password)\nPY",
        ]:
            self.assertIsNotNone(self.reason(command), command)


    # Regressions from 2026-10-24 (BUIL-CREDENTIAL-POLICY-m5rnsgd3): each was
    # refused with "Keychain credential access is prohibited" although none
    # reaches the Keychain.
    PAPERCLIP = 'https://github.com/paperclipai/paperclip.git'

    def test_public_clone_into_a_directory_made_in_the_same_call_is_allowed(self):
        # clone does no repository discovery, so an unresolvable -C target
        # does not change which helper runs; the URL decides.
        # The gh helper is approved for clone from github.com (2026-10-25).
        for command in [
            f'mkdir -p $S/src; git -C $S/src clone --depth 1 -q {self.PAPERCLIP}',
            f'mkdir -p $S/src; git -c credential.helper= -C $S/src clone --depth 1 -q {self.PAPERCLIP}',
            f'mkdir -p $S/src; GIT_TERMINAL_PROMPT=0 git -C $S/src -c credential.helper= clone --depth 1 -q {self.PAPERCLIP}',
        ]:
            with self.subTest(command=command):
                self.assertIsNone(self.decide(self.GH_SETUP, command))

    def test_clone_with_unresolvable_directory_still_judges_the_url(self):
        # Same shape, but the URL's host falls back to system osxkeychain.
        reason = self.decide(self.GH_SETUP, 'mkdir -p $S; git -C $S clone https://example.com/x.git')
        self.assertIn('osxkeychain', reason)
        self.assertIn('-c credential.helper=', reason)
        self.assertIsNone(self.decide(self.GH_SETUP,
                                      'mkdir -p $S; git -C $S -c credential.helper= clone https://example.com/x.git'))

    def test_unresolvable_git_directory_is_reported_as_such(self):
        for command, path in [('git -C $R push origin main', '$R'),
                              ('git -C /nonexistent/build-loop-test fetch', '/nonexistent/build-loop-test'),
                              ('git --git-dir=$G fetch', None)]:
            with self.subTest(command=command):
                reason = self.decide(self.GH_SETUP, command)
                self.assertIsNotNone(reason)
                if path:
                    self.assertIn(f'`{path}`', reason)
                    self.assertIn('not a Keychain finding', reason)

    def test_keychain_helper_refusal_names_context_helper_and_fix(self):
        reason = self.decide(['credential.helper osxkeychain', 'remote.origin.url https://example.com/r.git'],
                             'git push origin main')
        self.assertIn('https://example.com/r.git', reason)
        self.assertIn('`osxkeychain`', reason)
        self.assertIn('git -c credential.helper= push', reason)

    def test_help_and_version_with_redirects_are_allowed(self):
        for command in ['claude --help 2>&1 | grep -i plugin', 'claude --version', 'claude -v',
                        'claude --help >/dev/null 2>&1', 'claude mcp --help', 'claude help mcp',
                        'gh --help 2>&1', 'gh pr --help', 'gh help auth', 'gh --version 2> /dev/null']:
            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))

    def test_command_lookup_does_not_execute_the_named_program(self):
        commands = [
            'command -v claude', 'command -V claude',
            'command -pv claude', 'command -p -V claude',
            'builtin command -v claude', 'env command -v gh',
            'command -v -- security', 'command -v claude gh security',
            'command -v claude >/dev/null 2>&1',
            'command -v claude || true',
            'command -v claude; printf hello | cat',
            "command -v claude\nsed -n '1,2p' README.md",
        ]
        with patch('credential_policy.subprocess.run', side_effect=AssertionError('executed')):
            for command in commands:
                with self.subTest(command=command):
                    self.assertIsNone(self.reason(command))

    def test_command_lookup_cannot_hide_execution(self):
        commands = [
            'command claude -p hello', 'command -p claude -p hello',
            'command -- claude -p hello', 'command -v --; claude -p hello',
            'command -v claude && security dump-keychain',
            'command -v claude\nclaude -p hello',
            'command -v "$(security find-generic-password)"',
            '$(command -v claude) -p hello',
            'command -v claude | sh', 'command -v claude | cat | sh',
            'command -v claude | xargs',
            'command -v claude >/tmp/lookup-script; sh /tmp/lookup-script',
            'command -v claude >|/tmp/lookup-script; sh /tmp/lookup-script',
            '(command -v claude) | sh', '{ command -v claude; } | sh',
            '>/tmp/lookup-script command -v claude; sh /tmp/lookup-script',
            'if true; then command -v claude; fi | sh',
            'for x in once; do command -v claude; done | sh',
            'while false; do command -v claude; done | sh',
        ]
        with patch('credential_policy.subprocess.run', side_effect=AssertionError('executed')):
            for command in commands:
                with self.subTest(command=command):
                    self.assertIsNotNone(self.reason(command))

    def test_fd_duplication_is_not_a_command_separator_but_background_is(self):
        for command in ['claude --help 2>&1; security find-generic-password',
                        'claude --help & security find-generic-password',
                        'claude --help 2>&1 && claude -p hi',
                        'true 2>& 1; security dump-keychain',
                        'gh --version >&2 | gh auth token']:
            with self.subTest(command=command):
                self.assertIsNotNone(self.reason(command))

    def test_claude_session_and_auth_are_refused_with_an_accurate_reason(self):
        for command in ['claude -p hello', 'claude auth login', 'claude', 'claude --help -p hi']:
            with self.subTest(command=command):
                reason = self.reason(command)
                self.assertIsNotNone(reason)
                self.assertIn('Claude Code login', reason)

    def test_gh_is_refused_even_when_a_token_file_exists(self):
        # MAJ-06: a plaintext oauth_token for ANY host disabled gh protection
        # for every host. The owner rule bans plaintext substitution too.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'hosts.yml').write_text('example.com:\n    oauth_token: synthetic\n'
                                              'github.com:\n    user: someone\n')
            env = {'GH_CONFIG_DIR': tmp}
            for command in ['gh auth token --hostname github.com', 'gh pr list', 'gh api user']:
                with self.subTest(command=command):
                    reason = command_reason(command, platform='darwin', helpers=[], environment=env)
                    self.assertIsNotNone(reason)
                    self.assertIn('Keychain', reason)
            self.assertIsNone(command_reason('gh pr --help', platform='darwin', helpers=[], environment=env))

    def test_direct_keychain_tools_name_the_trigger(self):
        for command, trigger in [('security find-generic-password -w', '`security`'),
                                 ('security add-generic-password -s x -a y -w z', '`security`'),
                                 ('security delete-generic-password -s x', '`security`'),
                                 ('security unlock-keychain', '`security`'),
                                 ('security dump-keychain', '`security`'),
                                 ('security export -k login.keychain', '`security`'),
                                 ('security set-key-partition-list -S apple:', '`security`'),
                                 ('git credential-osxkeychain get', 'credential-osxkeychain'),
                                 ('/usr/local/bin/git-credential-osxkeychain get', 'credential-osxkeychain'),
                                 ('echo "$(security find-internet-password)"', '`security`'),
                                 ("osascript -e 'SecKeychainFindGenericPassword()'", 'Keychain API'),
                                 ("python3 -c 'import keyring; keyring.get_password(\"a\", \"b\")'", 'Keychain API')]:
            with self.subTest(command=command):
                reason = self.reason(command)
                self.assertIsNotNone(reason)
                self.assertIn(trigger, reason)

    # Independent review of e7f447fd (2026-10-25, BLK-01..MIN-07). Each shape
    # was allowed (or wrongly refused) before this change.
    def test_gh_helper_is_approved_only_for_fetch_pull_push_clone_to_github(self):
        gh = self.GH_SETUP + ['remote.origin.url https://github.com/o/r.git']
        for command in ['git fetch origin', 'git pull', 'git push origin main',
                        'git clone https://github.com/o/private.git']:
            with self.subTest(command=command):
                self.assertIsNone(self.decide(gh, command))
        for command in ['git ls-remote origin',
                        'git remote update', 'git submodule update --remote', 'git credential fill']:
            with self.subTest(command=command):
                reason = self.decide(gh, command)
                self.assertIsNotNone(reason)
                self.assertIn('`push` and `clone`', reason)
        self.assertIsNotNone(self.decide(gh + ['alias.sync fetch --all'], 'git sync'))
        # gh setup for gist.github.com is not part of the ruling.
        gist = ['credential.helper osxkeychain', 'credential.https://gist.github.com.helper ',
                'credential.https://gist.github.com.helper !gh auth git-credential',
                'remote.origin.url https://gist.github.com/abc.git']
        self.assertIsNotNone(self.decide(gist, 'git push origin main'))

    def test_clone_approval_is_limited_to_github(self):
        # Owner ruling 2026-10-25: clone joins fetch/pull/push, nothing wider.
        self.assertIsNone(self.decide(self.GH_SETUP, 'git clone https://github.com/o/r.git dest'))
        for url in ['https://gitlab.com/o/r.git', 'https://gist.github.com/abc.git',
                    'https://github.com:8443/o/r.git']:
            with self.subTest(url=url):
                reason = self.decide(self.GH_SETUP, f'git clone {url}')
                self.assertIsNotNone(reason)
                self.assertIn('-c credential.helper=', reason)
                self.assertIsNone(self.decide(self.GH_SETUP, f'git -c credential.helper= clone {url}'))
        # Submodules may reach any host; an extra helper joins the chain.
        self.assertIsNotNone(self.decide(self.GH_SETUP, 'git clone --recurse-submodules https://github.com/o/r.git'))
        self.assertIsNotNone(self.decide(self.GH_SETUP,
                                         'git clone -c credential.helper=osxkeychain https://github.com/o/r.git'))
        # gh CLI itself stays refused.
        for command in ['gh repo clone o/r', 'gh auth status', 'gh auth git-credential get']:
            with self.subTest(command=command):
                self.assertIsNotNone(self.reason(command))

    def ssh_config(self, tmp, text):
        path = Path(tmp) / 'config'
        path.write_text(text)
        return [(path, Path(tmp))]

    def test_ssh_that_may_search_the_keychain_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = self.ssh_config(tmp, 'Host *\n  IdentitiesOnly yes\n')
            with patch('credential_policy._ssh_config_files', return_value=clean):
                for command in ['ssh -o UseKeychain=yes git@github.com', 'ssh -oUseKeychain=yes host',
                                "ssh -o 'UseKeychain yes' host", 'scp -o UseKeychain=yes a host:b',
                                'ssh-add --apple-use-keychain key', 'ssh-add -K key', 'ssh-add --apple-load-keychain',
                                'ssh -F /nonexistent/ssh-config host', 'ssh -o UseKeychain=$X host',
                                "rsync -e 'ssh -o UseKeychain=yes' a host:b"]:
                    with self.subTest(command=command):
                        self.assertIn('UseKeychain=no', self.reason(command))
                for command in ['ssh host uptime', 'ssh-add -l', 'scp a host:b', 'rsync -a x host:y']:
                    with self.subTest(command=command):
                        self.assertIsNone(self.reason(command))
            unsafe = self.ssh_config(tmp, 'Host github.com\n  UseKeychain yes\n')
            with patch('credential_policy._ssh_config_files', return_value=unsafe):
                self.assertIn('UseKeychain yes', self.reason('ssh git@github.com'))
                self.assertIsNone(self.reason('ssh -o UseKeychain=no git@github.com'))  # first value wins
            include = self.ssh_config(tmp, 'Include ${CONF}/*.conf\n')
            with patch('credential_policy._ssh_config_files', return_value=include):
                self.assertIsNotNone(self.reason('ssh host'))
            nested = Path(tmp) / 'extra.conf'
            nested.write_text('UseKeychain=yes\n')
            included = self.ssh_config(tmp, 'Include extra.conf\n')
            with patch('credential_policy._ssh_config_files', return_value=included):
                self.assertIn('extra.conf', self.reason('ssh host'))

    def test_git_over_ssh_is_judged_by_its_ssh_settings(self):
        ssh_remote = ['credential.helper osxkeychain', 'remote.origin.url git@gitlab.com:o/r.git']
        for command, environment in [
                ("GIT_SSH_COMMAND='ssh -o UseKeychain=yes' git ls-remote ssh://git@github.com/o/r.git", None),
                ("git -c credential.helper= -c core.sshCommand='ssh -o UseKeychain=yes' "
                 "ls-remote ssh://git@github.com/o/r.git", None),
                ('git fetch origin', {'GIT_SSH_COMMAND': 'ssh -o UseKeychain=yes'}),
                ('git fetch origin', {'GIT_SSH': '/usr/local/bin/plink'}),
                ('git fetch origin', {'GIT_SSH_COMMAND': 'ssh $OPTS'})]:
            with self.subTest(command=command, environment=environment):
                self.assertIsNotNone(self.decide(ssh_remote, command, environment))
        self.assertIsNotNone(self.decide(ssh_remote + ['core.sshCommand ssh -o UseKeychain=yes']))
        self.assertIsNone(self.decide(ssh_remote))
        with tempfile.TemporaryDirectory() as tmp:
            self.ssh_files = self.ssh_config(tmp, 'Host *\n  UseKeychain yes\n')
            try:
                self.assertIn('UseKeychain yes', self.decide(ssh_remote))
                self.assertIsNone(self.decide(ssh_remote, 'git fetch origin',
                                              {'GIT_SSH_COMMAND': 'ssh -o UseKeychain=no'}))
            finally:
                del self.ssh_files

    def test_shell_expansion_in_the_command_word_fails_closed(self):
        for command in ["$'security' find-generic-password -w", 'sec${EMPTY}urity find-generic-password -w',
                        "zsh -c $'security find-generic-password -w'", '/usr/bin/secur* find-generic-password',
                        'secu{rity,} find-generic-password', '"$TOOL" find-generic-password',
                        'eval "$CMD"', 'git $SUB origin', "git -c $'credential.helper=osxkeychain' fetch"]:
            with self.subTest(command=command):
                reason = self.reason(command)
                self.assertIsNotNone(reason)
                self.assertIn('literally', reason)
        for command in ['python3 "$ROOT/x.py"', '[ -f x ] && echo y', '[[ -n $x ]] && echo y',
                        'for f in *.py; do echo "$f"; done', 'ls *.txt', 'echo {a,b}',
                        'case "$1" in\n  a) echo a ;;\n  *) echo other ;;\nesac']:
            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))
        self.assertIsNotNone(self.reason('case $x in a) (security dump-keychain) ;; esac'))

    def test_git_shell_bodies_and_executed_values_are_inspected(self):
        for command in ["git -c credential.helper= submodule foreach 'security find-generic-password -w'",
                        "git submodule foreach --recursive security dump-keychain",
                        "git rebase --exec 'security find-generic-password -w' HEAD~1",
                        "git rebase -x 'security dump-keychain' HEAD~1",
                        "git rebase --exec='security dump-keychain' HEAD~1",
                        'git bisect run security dump-keychain',
                        "git filter-branch --tree-filter 'security dump-keychain' HEAD",
                        "git difftool -x 'security dump-keychain'",
                        "git -c core.pager='security find-generic-password -w' log",
                        "git -c alias.x='!security dump-keychain' status",
                        "PAGER='security dump-keychain' git log",
                        "export GIT_PAGER='security dump-keychain'; git log",
                        'timeout 5 security find-generic-password', 'xargs -I{} security find-generic-password',
                        'nice -n 5 security dump-keychain', 'find . -exec security dump-keychain \\;']:
            with self.subTest(command=command):
                self.assertIn('security', self.reason(command))
        for command in ["git submodule foreach 'git status'", 'git -c core.pager=less log',
                        'git rebase --exec "make test" HEAD~2', 'find . -name x -exec grep -l foo {} +',
                        'timeout 5 make test']:
            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))

    def test_environment_changes_that_persist_into_git_are_refused(self):
        gh = self.GH_SETUP + ['remote.origin.url https://github.com/o/r.git']
        for command in ['unset HOME; git fetch origin', 'command unset HOME; git fetch origin',
                        'export -n HOME; git fetch origin', 'unset XDG_CONFIG_HOME && git push origin main',
                        'unset GIT_CONFIG_GLOBAL; git fetch origin', 'source ./env.sh; git fetch origin',
                        '. ./env.sh; git fetch origin', 'export GIT_SSH_COMMAND=ssh; git fetch origin']:
            with self.subTest(command=command):
                self.assertIn('separate calls', self.decide(gh, command))
        self.assertIsNone(self.decide(gh, 'unset FOO; git fetch origin'))
        # env -u HOME is modeled: git then reads system config (osxkeychain).
        self.assertIsNotNone(self.decide(['credential.helper osxkeychain'], 'env -u HOME git fetch origin'))

    def test_redirect_targets_are_files_not_commands(self):
        for command in ['claude --help >|security', 'claude --help >&security', 'claude --help > security',
                        'claude --help 2>|security', 'claude --help >& security']:
            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))
        for command in ['claude --help | security dump-keychain', 'claude --help || security dump-keychain',
                        'claude --help & security dump-keychain']:
            with self.subTest(command=command):
                self.assertIsNotNone(self.reason(command))



if __name__ == '__main__':
    unittest.main()
