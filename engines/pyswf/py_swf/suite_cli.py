"""Console entry point for the same transactional suite used by the desktop."""
from __future__ import annotations

import argparse
import json
import sys

from .universal import UniversalSuite, read_bounded, MAX_SOURCE


OPERATIONS = ('engines', 'inspect', 'scripts', 'resources', 'resource-info', 'source-files',
              'source-edit', 'resource-edit', 'edit', 'decompile', 'compare', 'assemble',
              'compile', 'compile-saved', 'validate', 'export', 'save-project',
              'undo', 'redo', 'snapshot', 'code-documents', 'compile-document', 'rpc')
MUTATIONS = {'source-edit', 'resource-edit', 'edit', 'undo', 'redo', 'snapshot'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='flash-suite', description='PySWF/RustSWF suite, sin interfaz gráfica')
    parser.add_argument('operation', choices=OPERATIONS)
    parser.add_argument('--input', help='SWF, proyecto .pyswfproj/.flashproj, entrada fuente o corpus')
    parser.add_argument('--unit', help='Identidad de una unidad de bytecode')
    parser.add_argument('--document', help='Identidad de una fuente guardada; por defecto project:entry al editar')
    parser.add_argument('--engine', default='auto')
    parser.add_argument('--source', help='Archivo de texto fuente o P-code')
    parser.add_argument('--profile', help='Perfil de compilación o benchmark; conserva el guardado al recompilar')
    parser.add_argument('--output', help='Destino nuevo de SWF/exportación/proyecto')
    parser.add_argument('--project-output', help='Guardar también la sesión editada en un proyecto nuevo')
    parser.add_argument('--state-dir', help='Directorio persistente de recuperación para invocaciones sucesivas')
    parser.add_argument('--format', default='resource-bundle')
    parser.add_argument('--tag-index', type=int)
    parser.add_argument('--name', help='Nombre del snapshot')
    parser.add_argument('--rpc-operation', help='Operación JSONL para rpc')
    parameters = parser.add_mutually_exclusive_group()
    parameters.add_argument('--args-json', help='Objeto JSON de argumentos adicionales')
    parameters.add_argument('--args-file', help='Archivo UTF-8 con un objeto JSON de argumentos')
    args = parser.parse_args(argv)
    rpc_mutations = {operation.replace('-', '_') for operation in MUTATIONS} | {'restore_snapshot'}
    if (args.operation in MUTATIONS or (args.operation == 'rpc' and args.rpc_operation in rpc_mutations)) and not (args.state_dir or args.project_output):
        parser.error('Una edición requiere --project-output o --state-dir para conservar la sesión')
    if args.operation in ('compile', 'compile-saved', 'compile-document', 'assemble', 'export', 'save-project') and not args.output:
        parser.error('Esta operación requiere --output con un destino nuevo')
    if args.operation in ('source-edit', 'edit', 'assemble', 'compile-document') and not args.source:
        parser.error('Esta operación requiere --source')
    if args.operation == 'rpc' and not args.rpc_operation:
        parser.error('rpc requiere --rpc-operation')
    if args.operation == 'compile' and not args.input:
        parser.error('Esta operación requiere --input')

    suite = None
    try:
        additional = json.loads(read_bounded(args.args_file, MAX_SOURCE).decode('utf-8-sig')) if args.args_file else json.loads(args.args_json or '{}')
        if not isinstance(additional, dict):
            raise ValueError('Los argumentos deben ser un objeto JSON')
        if (args.operation == 'rpc' and args.rpc_operation == 'compile_code_document'
                and additional.get('commit') and not (args.state_dir or args.project_output)):
            raise ValueError('Una edición requiere --project-output o --state-dir para conservar la sesión')
        suite = UniversalSuite(state_dir=args.state_dir)
        if args.input and args.operation != 'compile':
            suite.open(args.input)
        if args.operation == 'compile':
            result = suite.compile_project({**additional, 'entry':args.input, 'profile':args.profile or 'auto-as3', 'output':args.output})
        elif args.operation == 'code-documents':
            result = suite.code_documents({**additional, 'engine': args.engine,
                **({'document_id': args.document} if args.document else {})})
        elif args.operation == 'compile-document':
            if not args.document:
                raise ValueError('Selecciona --document para recompilar clase/script')
            document = suite.code_documents({'document_id': args.document, 'engine': 'native'})
            result = suite.compile_code_document({**additional, 'document_id': args.document,
                'source': read_bounded(args.source, MAX_SOURCE).decode('utf-8-sig'), 'output': args.output,
                'base_revision': suite.state()['revision'], 'base_source_sha256': document['original_source_sha256'],
                'tag_sha256': document['tag_sha256'], 'source_engine': args.engine})
        elif args.operation == 'compile-saved':
            result = suite.compile_saved({**additional, 'output':args.output, **({'profile':args.profile} if args.profile else {})})
        elif args.operation in ('decompile', 'compare'):
            result = suite.dispatch(args.operation, {**additional,'unit_id':args.unit,'engine':args.engine})
        elif args.operation in ('assemble', 'edit'):
            source = read_bounded(args.source, MAX_SOURCE).decode('utf-8-sig')
            result = suite.edit({**additional, 'unit_id':args.unit, 'code':source,
                                 'mode':'pcode' if args.operation == 'assemble' else additional.get('mode','source'),
                                 'base_revision':suite.state()['revision']})
            if args.output:
                result = suite.dispatch('export_swf', {'path':args.output})
        elif args.operation == 'source-edit':
            identity = args.document or 'project:entry'
            document = suite.dispatch('source_files', {'id':identity})
            result = suite.source_edit({**additional, 'id':identity,
                                        'source':read_bounded(args.source, MAX_SOURCE).decode('utf-8-sig'),
                                        'base_revision':suite.state()['revision'], 'base_source_sha256':document['source_sha256']})
        elif args.operation in ('resource-info', 'resource-edit'):
            if args.tag_index is not None:
                additional['tag_index'] = args.tag_index
            if args.operation == 'resource-edit':
                additional.setdefault('base_revision', suite.state()['revision'])
            result = suite.dispatch(args.operation.replace('-', '_'), additional)
        elif args.operation == 'source-files':
            if args.document:
                additional['id'] = args.document
            result = suite.dispatch('source_files', additional)
        elif args.operation == 'export':
            result = suite.export({**additional, 'format':args.format, 'path':args.output, 'engine':args.engine})
        elif args.operation == 'save-project':
            result = suite.dispatch('save_project', {'path':args.output})
        elif args.operation == 'snapshot':
            result = suite.dispatch('snapshot', {**additional, 'name':args.name or additional.get('name')})
        elif args.operation == 'rpc':
            result = suite.dispatch(args.rpc_operation, additional)
        else:
            result = suite.dispatch({'inspect':'state'}.get(args.operation, args.operation), additional)
        if args.project_output:
            saved = suite.dispatch('save_project', {'path':args.project_output})
            result = {'result':result, 'project':saved}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if suite:
            suite.close()


if __name__ == '__main__':
    raise SystemExit(main())
