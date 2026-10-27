"""Minimal trusted extension seam, on an existing schema. No live model/login.

Replace the scripted provider with the project's fixed model provider when
implementing a colleague's role. AgentRegistration is host code, not user input.
This example only reads ProductModel at a chosen confirmed data version.
"""
import argparse
import json

from ontologics.database import DatabaseSettings
from ontologics.contracts import AgentTask
from ontologics.harness import PinnedContext
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.runtime import ModelConfig
from ontologics.tool_specs import function_tools


EXTENSION = AgentRegistration(
    name='extension_demo',
    instructions='Read the exact supplied product model version. Preserve evidence and unknowns. Never approve or apply.',
    allowed_tools=frozenset({'lookup'}),
)


class ExampleProvider:
    """A contract demonstration; its output does not evaluate LLM quality."""
    def __init__(self, version):
        self.version, self.calls = version, 0

    def public_config(self):
        return {'provider': 'scripted-extension-example'}

    def __call__(self, *, model, messages):
        self.calls += 1
        if self.calls == 1:
            return {'text': '', 'tool_calls': [{'name': 'lookup',
                'arguments': {'kind': 'ProductModel', 'version': self.version}}]}
        return {'text': 'This scripted extension read the supplied version. Inspect the tool trace for source data.',
                'tool_calls': []}


def run_example(database, *, version=1):
    identities = DevelopmentIdentityProvider([UserIdentity('dev-extension-reader', frozenset({'read'}))], enabled=True)
    host = CommonHost(identities, database, agent_registrations=(EXTENSION,))
    # Pass function_tools(EXTENSION.allowed_tools) to the existing real provider
    # in a live integration. Advertising tools never bypasses gateway checks.
    with host.request(DevelopmentContext('dev-extension-reader')) as request:
        result = request.run_agent(EXTENSION.name, ExampleProvider(version),
            [{'role': 'user', 'content': 'Read ProductModel at data version ' + str(version)}],
            config=ModelConfig(model='scripted-extension-example'), capture_tool_trace=True)
        return {'mode': 'development identity + scripted provider; not real authentication or model evaluation',
                'role': EXTENSION.name, 'provider_tools': [t['name'] for t in function_tools(EXTENSION.allowed_tools)],
                'request_context': request.context, 'runtime': result}


def run_contract_example(database, *, version=1, flow='model', provider=None):
    """Two role-owned flows, one host/runtime. Defaults never call a paid model.

    A trusted caller can pass the existing OpenAIProvider with SPEC.requested_tools.
    User input must not select providers, identities, registrations or permissions.
    """
    if flow == 'model':
        from examples.agents.model_reader import SPEC, ModelReader, ScriptedModelReader
        role = ModelReader()
        task = AgentTask({'question': 'Read ProductModel at the pinned data version.'},
                         PinnedContext('Read product model', version))
        provider = provider if provider is not None else ScriptedModelReader(task)
    elif flow == 'history':
        from examples.agents.history_reader import SPEC, HistoryReader, ScriptedHistoryReader
        from ontologics.demo import example_queries
        role = HistoryReader()
        task = AgentTask({'purpose': 'Read fictional L07 installation history.', 'modes': ['A', 'B']},
                         PinnedContext('Read installation history; no defect/safety conclusion', version,
                                       conditions={'queries': example_queries()}))
        provider = provider if provider is not None else ScriptedHistoryReader(task)
    else:
        raise ValueError('Select model or history flow')
    identities = DevelopmentIdentityProvider([UserIdentity('dev-extension-reader', frozenset({'read'}))], enabled=True)
    host = CommonHost(identities, database, agent_registrations=(AgentRegistration.from_spec(SPEC),))
    with host.request(DevelopmentContext('dev-extension-reader')) as request:
        settings = getattr(provider, 'settings', None)
        model = settings.model if settings is not None else 'scripted-extension-example'
        return request.run_task(SPEC.name, role, task, provider,
            config=ModelConfig(model=model), capture_tool_trace=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', required=True, help='Existing development schema with the requested snapshot')
    parser.add_argument('--version', type=int, default=1)
    parser.add_argument('--env-file')
    parser.add_argument('--typed', action='store_true', help='Demonstrate the versioned AgentSpec/Task/Result contract')
    parser.add_argument('--flow', choices=['model', 'history'], default='model',
                        help='Typed example: one lookup or sequential A/B common-tool calls')
    args = parser.parse_args()
    database = DatabaseSettings.from_environment(schema=args.schema, env_file=args.env_file)
    result = (run_contract_example(database, version=args.version, flow=args.flow) if args.typed
              else run_example(database, version=args.version))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['runtime']['ok'] else 1)


if __name__ == '__main__':
    main()
