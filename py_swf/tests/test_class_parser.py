import pytest

from py_swf.compiler.class_parser import (
    ClassDecl,
    FieldDecl,
    MetadataDecl,
    MethodDecl,
    ParameterDecl,
    Program,
    parse_class_program,
)


def test_parse_class_fields_methods_metadata_and_defaults():
    source = '''package game.world {
        import flash.display.Sprite;
        [Bindable]
        public final class Hero extends Sprite implements IAlive, game.ITrace {
            [Embed(source="hero.png", mimeType="image/png")]
            private static const asset:Class = Asset("hero.png");
            internal var health:int = 100;
            public function Hero(name:String = "a,b", ...rest:*):void { trace(name); }
            [Event(name="ready", type="Event")]
            public function get title():String { return "hero"; }
            public function set title(value:String):void { trace(value); }
            public function heal(amount:int = (1 + 2)):int;
        }
    }'''
    program = parse_class_program(source)

    assert isinstance(program, Program)
    assert program.package == "game.world"
    assert program.imports == ["flash.display.Sprite"]
    hero = program.classes[0]
    assert isinstance(hero, ClassDecl)
    assert (hero.name, hero.visibility, hero.modifiers) == ("Hero", "public", ("public", "final"))
    assert hero.super_name == "Sprite"
    assert hero.interfaces == ["IAlive", "game.ITrace"]
    asset, health, ctor, getter, setter, declaration = hero.members
    assert isinstance(asset, FieldDecl)
    assert (asset.name, asset.type_name, asset.visibility, asset.modifiers, asset.constant) == (
        "asset", "Class", "private", ("private", "static"), True
    )
    assert asset.value_source == 'Asset("hero.png")'
    assert asset.metadata == [MetadataDecl("Embed", [("source", '"hero.png"'), ("mimeType", '"image/png"')])]
    assert (health.type_name, health.value_source) == ("int", "100")
    assert isinstance(ctor, MethodDecl)
    assert [ParameterDecl("name", "String", '"a,b"'), ParameterDecl("rest", "*", None, True)] == ctor.parameters
    assert ctor.return_type == "void"
    assert ctor.body_source == " trace(name); "
    assert getter.accessor == "get" and setter.accessor == "set"
    assert getter.metadata == [MetadataDecl("Event", [("name", '"ready"'), ("type", '"Event"')])]
    assert declaration.body_source is None
    assert declaration.parameters[0].default_source == "(1 + 2)"


def test_package_block_without_name_and_interface_inheritance():
    program = parse_class_program("package { public interface IChild extends IBase, other.I { function read():String; } }")
    assert program.package == ""
    assert program.classes[0].modifiers == ("public", "interface")
    assert program.classes[0].interfaces == ["IBase", "other.I"]
    assert program.classes[0].members[0].body_source is None


def test_nested_braces_comments_and_strings_are_preserved_in_body_slice():
    body = '''
            var s:String = "} // not a comment"; /* { ignored */
            if (ok) { trace("{ still a string }"); // } ignored
                nested();
            }
        '''
    parsed = parse_class_program("class A { function run():void {" + body + "} }")
    assert parsed.classes[0].members[0].body_source == body


@pytest.mark.parametrize(
    "source, message",
    [
        ("package { public class A {", "Unclosed class/interface body"),
        ('class A { function f():void { trace("oops); } }', "Unterminated string"),
        ("class A { var x:int = ; }", "Expected a source value|Expected a field initializer"),
        ("class A { public private var x:int; }", "Conflicting visibility"),
        ("class A { function f(a:int, a:String):void; }", "Duplicate parameter"),
        ("package p { import x.*; public class A {} }", "Wildcard imports"),
        ("class A extends Vector.<int> {}", "Generics"),
        ("class A { function f():void { <node/>; } }", "XML/E4X"),
        ("class A { var x:int; var x:String; }", "Conflicting duplicate member"),
    ],
)
def test_malformed_or_out_of_tier_source_has_diagnostic(source, message):
    with pytest.raises(SyntaxError, match=message):
        parse_class_program(source)


def test_get_and_set_accessors_with_same_name_are_valid_but_duplicates_are_not():
    program = parse_class_program(
        "class A { function get value():int; function set value(v:int):void; }"
    )
    assert [member.accessor for member in program.classes[0].members] == ["get", "set"]
    with pytest.raises(SyntaxError, match="Duplicate get accessor"):
        parse_class_program("class A { function get value():int; function get value():int; }")


def test_metadata_keeps_source_literal_spelling_and_comments_are_ignored_by_lexer():
    program = parse_class_program('''// header
    package p { [Info(text="a\\n", count=0x10)] class A { /*m*/ var x:*; } }
    ''')
    assert program.classes[0].metadata[0].items == [("text", '"a\\n"'), ("count", "0x10")]


@pytest.mark.parametrize("source", ["class A { /* unfinished", "class A { function f():void { /* x */"])
def test_unterminated_comments_and_method_bodies_are_rejected(source):
    with pytest.raises(SyntaxError):
        parse_class_program(source)
