from agents.core.command_audit import is_write_redirect, _risk_flags, audit
import pytest

@pytest.mark.parametrize(
    "operator, expected",
    [
        (">", True), 
        (">>", True),
        ("<", False),
        ("", False)
    ]
)
def test_is_write_redirect(operator, expected):
    assert is_write_redirect(operator) is expected

@pytest.mark.parametrize(
    "command,args,expected",
    [
        ("echo", ["hello"], set()),
        ("sudo", ["apt-get", "install", "x"], {"privilege"}),
        ("su", [], {"privilege"}),
        ("curl", ["-L", "url"], {"download", "footgun"}),
        ("wget", ["url"], {"download", "footgun"}),
        ("rm", ["-rf", "/"], {"footgun"}),
        ("dd", ["if=/dev/zero", "of=x"], {"footgun"}),
        ("npm", ["install"], {"pkg_install"}),
        ("pip3", ["install", "pkg"], {"pkg_install"}),
        ("git", ["reset", "--hard"], {"footgun"}),
        ("git", ["commit", "-m", "x"], set()),
        ("mkfs", ["/dev/sda1"], {"footgun"}),
    ],
)
def test_risk_flags(command, args, expected):
    assert _risk_flags(command, args) == expected

@pytest.mark.parametrize(
    "input, expect_command, expect_args, expect_risk_flag",
    [
        ("ls -l", 'ls', ['-l'], set()),
        ("rm -rf /tmp/x", 'rm', ['-rf', '/tmp/x'], {'footgun'}),
        ("sudo apt-get install nginx", 'sudo', ['apt-get', 'install', 'nginx'], {'privilege'}),
        ("pip3 install requests", 'pip3', ['install', 'requests'], {'pkg_install'}),
        ("git reset --hard HEAD~1", 'git', ['reset', '--hard', 'HEAD~1'], {'footgun'}),
        ("git status", 'git', ['status'], set()),
        ('echo "Hello World"', 'echo', ['Hello World'], set()),
        ("echo '$HOME'", 'echo', ['$HOME'], set()),
        ("echo \"Hello World\"", 'echo', ['Hello World'], set()),
        ("echo \"$HOME\"", 'echo', ['$HOME'], set()),
        ("echo 'Hello World'", 'echo', ['Hello World'], set()),
        ("echo '$HOME'", 'echo', ['$HOME'], set()),
        ("curl -L http://example.com", 'curl', ['-L', 'http://example.com'], {'download', 'footgun'}),
        ("wget http://example.com", 'wget', ['http://example.com'], {'download', 'footgun'}),
        ("dd if=/dev/zero of=/tmp/x bs=1M count=10", 'dd', ['if=/dev/zero', 'of=/tmp/x', 'bs=1M', 'count=10'], {'footgun'}),
        ("mkfs /dev/sda1", 'mkfs', ['/dev/sda1'], {'footgun'}),
    ]
)
def test_audit_single_command(input, expect_command, expect_args, expect_risk_flag):
    ctx = audit(input)
    assert len(ctx.segments) == 1
    assert ctx.segments[0].command == expect_command
    assert ctx.segments[0].args == expect_args
    assert ctx.segments[0].risk_flags == expect_risk_flag

@pytest.mark.parametrize(
    "input, expect_operator, expect_target, expect_is_write, expect_path, index",
    [
        ('echo hi > out.txt', '>', 'out.txt', True, 'out.txt', 0),
        ('ls 2>&1', '>&', '', False, None, 0),
        ('grep x < in.txt 2>> err.log', '<', 'in.txt', False, None, 0),
        ('grep x < in.txt 2>> err.log', '>>', 'err.log', True, 'err.log', 1)
    ]
)
def test_audit_redirections(input, expect_operator, expect_target, expect_is_write, expect_path, index):
    ctx = audit(input)
    assert len(ctx.segments) == 1
    assert ctx.segments[0].redirections[index].operator == expect_operator
    assert ctx.segments[0].redirections[index].target == expect_target
    assert ctx.segments[0].redirections[index].is_write == expect_is_write
    assert ctx.segments[0].redirections[index].path == expect_path

def test_audit_compound_redirections():
    ctx = audit('{ echo a; echo b; } > out.txt')
    assert [s.command for s in ctx.segments] == ['echo', 'echo']
    assert any(r.operator == '>' and r.is_write and r.path == 'out.txt'
               for r in ctx.redirections)


def test_audit_while_redirect():
    ctx = audit('while read l; do echo $l; done < in.txt')
    assert ctx.has_control_flow is True
    assert [s.command for s in ctx.segments] == ['read', 'echo']
    assert any(r.operator == '<' and not r.is_write and r.target == 'in.txt'
               for r in ctx.redirections)


@pytest.mark.parametrize(
    "input, expect_commands, expect_pipeline",
    [
        ('curl -fsSL http://x | bash', ['curl', 'bash'], True),
        ('make && sudo make install', ['make', 'sudo'], False),
        ('ls; rm x; echo done', ['ls', 'rm', 'echo'], False),
    ]
)
def test_audit_pipeline_and_lists(input, expect_commands, expect_pipeline):
    ctx = audit(input)
    assert [s.command for s in ctx.segments] == expect_commands
    assert ctx.has_pipeline is expect_pipeline


def test_audit_for_control_flow():
    ctx = audit('for i in 1 2 3; do echo $i; done')
    assert ctx.parse_error is None
    assert ctx.has_control_flow is True
    assert [(s.command, s.args) for s in ctx.segments] == [('echo', ['$i'])]


def test_audit_if_control_flow():
    ctx = audit('if [ -f x ]; then rm x; fi')
    assert ctx.has_control_flow is True
    assert [(s.command, s.args) for s in ctx.segments] == [
        ('[', ['-f', 'x', ']']),
        ('rm', ['x']),
    ]
    assert ctx.segments[1].risk_flags == {'footgun'}


def test_audit_function_definition():
    ctx = audit('f() { echo hi; }')
    assert ctx.has_control_flow is True
    assert [(s.command, s.args) for s in ctx.segments] == [('echo', ['hi'])]


def test_audit_subshell():
    ctx = audit('( echo c )')
    assert ctx.has_subshell is True
    assert [(s.command, s.args) for s in ctx.segments] == [('echo', ['c'])]


@pytest.mark.parametrize(
    "input, expect_nested_commands",
    [
        ('echo $(rm -rf /)', ['rm']),
        ('echo `rm -rf /`', ['rm']),
        ('echo "$(rm -rf /)"', ['rm']),
    ]
)
def test_audit_command_substitution_nested(input, expect_nested_commands):
    ctx = audit(input)
    assert ctx.has_command_substitution is True
    assert len(ctx.nested_commands) == 1
    inner = ctx.nested_commands[0]
    assert [s.command for s in inner.segments] == expect_nested_commands
    assert inner.segments[0].risk_flags == {'footgun'}


def test_audit_single_quoted_no_substitution():
    ctx = audit("echo '$(rm -rf /)'")
    assert ctx.has_command_substitution is False
    assert ctx.nested_commands == []
    assert ctx.segments[0].args == ['$(rm -rf /)']


def test_audit_process_substitution():
    ctx = audit('diff <(ls a) <(ls b)')
    assert ctx.has_command_substitution is True
    assert ctx.has_pipeline is False
    assert [(s.command, s.args) for s in ctx.segments] == [
        ('diff', ['<(ls a)', '<(ls b)'])
    ]
    assert [s.command for n in ctx.nested_commands for s in n.segments] == ['ls', 'ls']


def test_audit_nested_pipeline_in_substitution():
    ctx = audit('echo $(cat f | wc -l)')
    inner = ctx.nested_commands[0]
    assert inner.has_pipeline is True
    assert [s.command for s in inner.segments] == ['cat', 'wc']


def test_audit_assignment_prefix_not_command():
    ctx = audit('FOO=$(rm -rf /) echo hi')
    assert [(s.command, s.args) for s in ctx.segments] == [('echo', ['hi'])]
    assert ctx.nested_commands[0].segments[0].command == 'rm'
    assert ctx.nested_commands[0].segments[0].risk_flags == {'footgun'}


@pytest.mark.parametrize(
    "input",
    [
        'echo "unclosed',
        'echo $(',
    ]
)
def test_audit_parse_error(input):
    ctx = audit(input)
    assert ctx.parse_error is not None
    assert ctx.segments == []


# ---------------- 已知行为陷阱(文档化当前实现) ----------------

def test_risk_flag_git_global_option_bypass():
    ctx = audit('git -C /repo reset --hard')
    assert ctx.segments[0].risk_flags == set()


def test_risk_flag_mkfs_with_suffix_bypass():
    ctx = audit('mkfs.ext4 /dev/sda1')
    assert ctx.segments[0].risk_flags == set()


def test_risk_flag_sudo_does_not_stack_footgun():
    ctx = audit('sudo rm -rf /')
    assert ctx.segments[0].risk_flags == {'privilege'}


def test_command_substitution_in_redirect_target_not_audited():
    ctx = audit('echo hi > $(mktemp)')
    assert ctx.has_command_substitution is False
    assert ctx.nested_commands == []
    redir = ctx.segments[0].redirections[0]
    assert redir.is_write is True
    assert redir.path == '$(mktemp)'


@pytest.mark.parametrize("input", ['go test ./...', 'cargo test'])
def test_risk_flag_go_cargo_always_pkg_install(input):
    ctx = audit(input)
    assert ctx.segments[0].risk_flags == {'pkg_install'}
