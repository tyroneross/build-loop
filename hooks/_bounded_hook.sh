# SPDX-License-Identifier: Apache-2.0
# Source, then call bl_bound_hook before reading stdin or starting Python.
# Reuses the SessionStart supervisor contract for whole-invocation deadlines.
# Advisory hooks return {}; permission gates ask when their checks cannot finish.
bl_bound_hook() {
    local budget="$1" maximum="$2" output="$3"
    shift 3
    if [ "${BUILD_LOOP_BOUNDED_HOOK_CHILD:-}" = "$0" ]; then
        unset BUILD_LOOP_BOUNDED_HOOK_CHILD
        return 0
    fi
    local fallback='{}'
    if [ "$output" = gate ]; then
        fallback='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Build Loop checks did not finish. Retry the command so its required checks can run."}}'
    fi
    if ! command -v perl >/dev/null 2>&1; then
        printf '%s' "$fallback"
        exit 0
    fi
    exec perl -e '
        use strict;
        my ($ms, $maximum, $mode, $fallback, $parent, $hook) = splice @ARGV, 0, 6;
        my $started = eval { require Time::HiRes; Time::HiRes::time() }
            // do { print $fallback; exit 0; };
        eval { require POSIX; require JSON::PP; 1 }
            or do { print $fallback; exit 0; };
        $ms = $maximum unless $ms =~ /\A[0-9]+\z/ && $ms >= 100 && $ms <= $maximum;
        my $deadline = $started + $ms / 1000;
        $ENV{BUILD_LOOP_BOUNDED_HOOK_CHILD} = $hook;
        $ENV{BUILD_LOOP_RALLY_START_PARENT_PID} = $parent;
        my ($r, $w);
        pipe($r, $w) or do { print $fallback; exit 0; };
        my $pid = fork();
        defined($pid) or do { print $fallback; exit 0; };
        if (!$pid) {
            close $r;
            POSIX::setsid();
            open(STDOUT, ">&", $w) or exit 0;
            close $w;
            exec @ARGV;
            exit 1;
        }
        close $w;
        my $stop = sub {
            kill "TERM", -$pid;
            kill "TERM", $pid;
            # Reap cooperatively without ever extending the host budget.
            my $until = Time::HiRes::time() + 0.05;
            while (Time::HiRes::time() < $until) {
                last if waitpid($pid, POSIX::WNOHANG()) == $pid;
                Time::HiRes::sleep(0.005);
            }
        };
        $SIG{TERM} = $SIG{INT} = $SIG{HUP} = sub { $stop->(); exit 0; };
        my ($buf, $eof, $reaped, $status) = ("", 0, 0, 0);
        while (Time::HiRes::time() < $deadline) {
            my $left = $deadline - Time::HiRes::time();
            last if $left <= 0;
            my $rin = "";
            vec($rin, fileno($r), 1) = 1;
            my $n = select(my $rout = $rin, undef, undef, $left < 0.02 ? $left : 0.02);
            if ($n > 0 && !$eof) {
                my $got = sysread($r, $buf, 65536, length $buf);
                $eof = 1 if defined($got) && !$got;
                last if length($buf) > 65536;
            }
            if (!$reaped && waitpid($pid, POSIX::WNOHANG()) == $pid) {
                $reaped = 1;
                $status = $?;
            }
            if ($reaped && $eof) {
                if ($mode eq "text" && !$status) {
                    print $buf;
                    exit 0;
                }
                # Outside opted-in repos, closeout has always been silent.
                if ($mode eq "optional-json" && !$status && !length($buf)) {
                    exit 0;
                }
                my $value = eval { JSON::PP::decode_json($buf) };
                my $valid = !$@ && ref($value) eq "HASH";
                # Deliberate deny (exit 2) must survive the supervisor.
                my $rc = $status >> 8;
                if ($mode eq "gate" && ($status & 127 || ($rc != 0 && $rc != 2))) {
                    print $fallback;
                    exit 0;
                }
                print $valid ? $buf : $fallback;
                exit(($mode eq "gate" && $rc == 2) ? 2 : 0);
            }
        }
        $stop->();
        print $fallback;
        exit 0;
    ' "$budget" "$maximum" "$output" "$fallback" "${PPID:-0}" "$0" "$@"
}
