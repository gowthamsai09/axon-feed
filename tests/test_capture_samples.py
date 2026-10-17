from __future__ import annotations

import json

from scripts.capture_samples import safe_name, trim, trim_json, trim_xml

RSS = (
    "<rss><channel><title>t</title>"
    + "".join(f"<item><a>{i}</a></item>" for i in range(8))
    + "</channel></rss>"
)
ATOM = (
    '<feed xmlns="x"><title>t</title>'
    + "".join(f"<entry><id>{i}</id></entry>" for i in range(7))
    + "</feed>"
)


def test_trim_xml_keeps_header_footer_and_n_blocks() -> None:
    out = trim_xml(RSS, 3)
    assert (
        out.count("<item>") == 3
        and out.startswith("<rss><channel><title>t")
        and out.endswith("</channel></rss>")
    )
    assert trim_xml(ATOM, 2).count("<entry>") == 2
    assert trim_xml(RSS, 20) == RSS


def test_trim_json_and_dispatch() -> None:
    assert trim_json([1, 2, 3, 4], 2) == [1, 2]
    assert trim_json({"hits": [1, 2, 3], "n": 9}, 1) == {"hits": [1], "n": 9}
    ext, text = trim("api", json.dumps({"hits": list(range(10))}).encode(), 4)
    assert ext == ".json" and json.loads(text)["hits"] == [0, 1, 2, 3]
    assert trim("rss", RSS.encode(), 1)[0] == ".xml"


def test_safe_name_is_windows_safe() -> None:
    assert safe_name("github-releases[ollama/ollama]") == "github-releases__ollama__ollama"
    # dots map to a single underscore so a fresh capture matches the committed fixture name
    assert (
        safe_name("github-releases[ggml-org/llama.cpp]") == "github-releases__ggml-org__llama_cpp"
    )
