"""화면 스크립트 문법 검사.

JS 문법이 깨지면 화면이 통째로 빈 채로 뜬다. 서버도 API도 멀쩡해서 로그에 아무것도 안 남고,
테스트도 전부 통과한다. 실제로 그렇게 한 번 나갔다 — 문자열 안에 진짜 줄바꿈이 들어가
스크립트 전체가 죽었고, "DB가 안 뜬다" 로 보였다.

node 가 있으면 문법을 검사하고, 없으면 건너뛴다.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
PAGES = [
    REPO / "services" / "ops_api" / "static" / "index.html",
    REPO / "services" / "kiosk_api" / "static" / "index.html",
]
SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
# onkeydown="if(...)" 처럼 함수 호출이 아닌 것들
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof"}


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.parent.parent.name)
def test_화면_스크립트에_문법_오류가_없다(page: pathlib.Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")

    match = SCRIPT.search(page.read_text(encoding="utf-8"))
    assert match, f"{page.name} 에 script 블록이 없다"

    with tempfile.TemporaryDirectory() as workdir:
        target = pathlib.Path(workdir) / "page.js"
        target.write_text(match.group(1), encoding="utf-8")
        result = subprocess.run(
            [node, "--check", str(target)], capture_output=True, text=True, timeout=30
        )
    assert result.returncode == 0, f"{page.name} 문법 오류:\n{result.stderr[:600]}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.parent.parent.name)
def test_화면이_부르는_함수가_전부_정의되어_있다(page: pathlib.Path) -> None:
    """onclick="doThing()" 을 써놓고 함수를 안 만드는 실수를 잡는다."""
    html = page.read_text(encoding="utf-8")
    called = {name for name in re.findall(r'on\w+="(\w+)\(', html)}
    called |= {name for name in re.findall(r"on\w+='(\w+)\(", html)}
    defined = set(re.findall(r"function\s+(\w+)\s*\(", html))
    defined |= set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", html))
    missing = called - defined - JS_KEYWORDS
    assert not missing, f"{page.name} 에서 부르는데 정의가 없다: {sorted(missing)}"
