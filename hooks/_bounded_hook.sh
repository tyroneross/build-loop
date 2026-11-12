# SPDX-License-Identifier: Apache-2.0
# Source, then call bl_bound_hook before reading stdin or starting Python.
# Reuses the SessionStart supervisor contract for whole-invocation deadlines.
# Advisory hooks return {}; required gates deny when their checks cannot finish.
bl_bound_hook() {
    local budget="$1" maximum="$2" output="$3"
    shift 3
    if [ "${BUILD_LOOP_BOUNDED_HOOK_CHILD:-}" = "$0" ]; then
        unset BUILD_LOOP_BOUNDED_HOOK_CHILD
        return 0
    fi
    local fallback='{}'
    if [ "$output" = gate ]; then
        fallback='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Build Loop checks did not finish. Retry the command so its required checks can run."}}'
    fi
    if ! command -v perl >/dev/null 2>&1; then
        printf '%s' "$fallback"
        exit 0
    fi
    exec perl -e '
        use strict;
        my ($ms, $maximum, $mode, $fallback, $parent, $hook) = splice @ARGV, 0, 6;
        # Core alarm needs no imports: module loading must not outlive the host.
        $SIG{ALRM} = sub { print $fallback; exit 0; };
        alarm 1;
        my $started = eval { require Time::HiRes; Time::HiRes::time() }
            // do { print $fallback; exit 0; };
        eval { require POSIX; require JSON::PP; 1 }
            or do { print $fallback; exit 0; };
        $ms = $maximum unless $ms =~ /\A[0-9]+\z/ && $ms >= 100 && $ms <= $maximum;
        my $deadline = $started + $ms / 1000;
        alarm 0;
        $ENV{BUILD_LOOP_BOUNDED_HOOK_CHILD} = $hook;
        $ENV{BUILD_LOOP_RALLY_START_PARENT_PID} = $parent;
        my ($r, $w);
        pipe($r, $w) or do { print $fallback; exit 0; };
        # Install before fork: a cancellation between fork and registration
        # must still reach cleanup. Handlers only set a flag; cleanup stays in
        # ordinary control flow and cannot race another cleanup invocation.
        my $cancelled = 0;
        $SIG{TERM} = $SIG{INT} = $SIG{HUP} = sub { $cancelled = 1; };
        my $pid = fork();
        defined($pid) or do { print $fallback; exit 0; };
        if (!$pid) {
            $SIG{TERM} = $SIG{INT} = $SIG{HUP} = "DEFAULT";
            close $r;
            POSIX::setsid();
            open(STDOUT, ">&", $w) or exit 0;
            close $w;
            exec @ARGV;
            POSIX::_exit(1);
        }
        close $w;
        my ($buf, $eof, $reaped, $status) = ("", 0, 0, 0);
        my $stop = sub {
            kill "TERM", -$pid;
            kill "TERM", $pid unless $reaped;
            # Give the whole group a bounded grace, even if its leader exits.
            # A child that has not reached setsid is covered by the direct PID.
            my $until = Time::HiRes::time() + 0.05;
            while (Time::HiRes::time() < $until) {
                $reaped = 1 if !$reaped && waitpid($pid, POSIX::WNOHANG()) == $pid;
                Time::HiRes::sleep(0.005);
            }
            kill "KILL", -$pid;
            kill "KILL", $pid unless $reaped;
            # Reap our direct child without an unbounded wait on a stuck kernel
            # operation. Only workers detached into independent groups survive;
            # nohup alone does not detach a worker from this group.
            $until = Time::HiRes::time() + 0.02;
            while (!$reaped && Time::HiRes::time() < $until) {
                $reaped = 1 if waitpid($pid, POSIX::WNOHANG()) == $pid;
                Time::HiRes::sleep(0.001) unless $reaped;
            }
        };
        while (!$cancelled && Time::HiRes::time() < $deadline) {
            my $left = $deadline - Time::HiRes::time();
            last if $left <= 0;
            my $rin = "";
            vec($rin, fileno($r), 1) = 1 unless $eof;
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
            last if $cancelled;
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
        print $fallback unless $cancelled;
        exit 0;
    ' "$budget" "$maximum" "$output" "$fallback" "${PPID:-0}" "$0" "$@"
}
