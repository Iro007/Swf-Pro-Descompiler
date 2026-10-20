from py_swf.swf_parser import SWFTag


def test_newer_classic_tag_names_are_inspectable():
    assert SWFTag(38, b"\x01\x00").name == "DefineVideo"
    assert SWFTag(40, b"\x01\x00").name == "NameCharacter"
    assert SWFTag(93, b"").name == "EnableTelemetry"
    assert SWFTag(94, b"").name == "PlaceObject4"
