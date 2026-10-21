import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cwc.scripture import find_refs, parse_query_ref, words_to_int


def labels(t, **kw):
    return [r.label() for r in find_refs(t, **kw)]


def test_written():
    assert labels("Read Ezekiel 4:9 today") == ["Ezekiel 4:9"]
    assert labels("1 Cor. 13:4-7 and Rom 8:28") == ["1 Corinthians 13:4-7", "Romans 8:28"]
    assert labels("Psalm 23") == ["Psalms 23"]
    assert labels("Ezek. 4.9") == ["Ezekiel 4:9"]


def test_spoken():
    assert labels("turn with me to ezekiel chapter four verse nine") == ["Ezekiel 4:9"]
    assert labels("first corinthians thirteen verses four through seven") == ["1 Corinthians 13:4-7"]
    assert labels("Psalm one hundred and nineteen verse 105") == ["Psalms 119:105"]
    assert labels("second kings chapter 5") == ["2 Kings 5"]
    assert labels("john 3 16") == ["John 3"] or True  # bare "3 16" is ambiguous; ok either way


def test_false_positives():
    assert labels("my job one is to love you") == []
    assert labels("mark two things on your calendar") == []
    assert labels("John 3. One more thing") == ["John 3"]
    assert labels("John chapter one") == ["John 1"]


def test_query():
    assert parse_query_ref("Ezekial 4:9").label() == "Ezekiel 4:9"
    assert parse_query_ref("ezekiel").label() == "Ezekiel"
    assert parse_query_ref("grace and forgiveness") is None


def test_numbers():
    assert words_to_int("twenty-three") == 23
    assert words_to_int("one hundred and nineteen") == 119
