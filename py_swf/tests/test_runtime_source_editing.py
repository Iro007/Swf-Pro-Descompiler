from copy import deepcopy

from py_swf.avm1 import assemble_avm1
from py_swf.avm2 import ABCFile
from py_swf.benchmark_worker import decompile
from py_swf.editing import edit_script,source_edit_reason
from py_swf.swf_parser import SWFTag
from tests.native_fixtures import as3_tag,swf_with


def test_public_dynamic_script_method_can_be_edited_again_after_reopen():
    tag,_=edit_script(as3_tag(),"var values = [1,2]; return values[0];",1,source=True)
    abc=ABCFile()
    abc.parse(tag.parse_doabc()[2])
    assert source_edit_reason(abc,abc.method_bodies[1]) is None
    second,_=edit_script(tag,"var values = [4,5]; values[0] += 7; return values[0];",1,source=True)
    reread=ABCFile()
    reread.parse(second.parse_doabc()[2])
    assert source_edit_reason(reread,reread.method_bodies[1]) is None


def test_as2_bracket_source_is_not_checked_using_an_as3_pool_less_parser():
    tag,_=edit_script(SWFTag(12,assemble_avm1("push 1\ntrace\nend")),"var a = [1,2]; return a[0];",source=True)
    result=decompile(swf_with([tag]),include_source=True)
    unit=result["units"][0]
    assert unit["generation"]=="available"
    assert unit["syntax"]=="native_subset_supported"
    assert unit["compiler_eligible"]


def test_non_public_pool_still_protects_dynamic_source_edit():
    tag,_=edit_script(as3_tag(),"var values = [1,2]; return values[0];",1,source=True)
    abc=ABCFile()
    abc.parse(tag.parse_doabc()[2])
    before=deepcopy(abc.constant_pool.multinames)
    abc.constant_pool.namespaces.append({"kind":5,"name":0})
    assert source_edit_reason(abc,abc.method_bodies[1])
    assert abc.constant_pool.multinames==before
