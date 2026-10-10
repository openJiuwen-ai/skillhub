from plugins_market.validation.content_security import find_dangerous_script_content

_JS_REGEXP_EXEC = "const match = /LOGIN (.+)/.exec(cmd);\n"
_JS_MINIFIED_EXEC = "function n(e,t){return t.exec(e)}\n"


def test_js_regexp_exec_is_not_python_exec() -> None:
    assert find_dangerous_script_content(_JS_REGEXP_EXEC, filename="imap.bundle.js") is None
    assert find_dangerous_script_content(
        _JS_MINIFIED_EXEC, filename="skills/scripts/imap.bundle.js"
    ) is None


def test_python_exec_still_blocked() -> None:
    assert find_dangerous_script_content("exec(payload)\n", filename="tools/run.py") == (
        "exec 动态代码执行"
    )


def test_js_eval_and_child_process_exec_still_blocked() -> None:
    assert find_dangerous_script_content("eval(code)\n", filename="run.js") == (
        "eval 动态代码执行"
    )
    assert find_dangerous_script_content(
        'require("child_process").exec("ls")\n', filename="run.js"
    ) == "child_process 进程执行入口"
    assert find_dangerous_script_content(
        'new Function("return 1")()\n', filename="run.ts"
    ) == "Function 动态代码执行"


def test_js_bundled_sendmail_spawn_is_not_blocked() -> None:
    snippet = 'var spawn = require("child_process").spawn;\n'
    assert find_dangerous_script_content(snippet, filename="smtp.bundle.js") is None
