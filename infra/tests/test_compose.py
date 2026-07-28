"""The compose file, checked against the code it claims to run.

Twenty-nine services have been described here and never started: there is no
Docker daemon in the environment this was built in, and saying so in
ARCHITECTURE.md is honest but does nothing about the fact that a file nobody
has executed is a file full of plausible mistakes — a module path that moved, a
service pointing at a hostname no container answers to, a volume that was
renamed in one place.

None of this is a substitute for `docker compose up`. What it does is catch
every class of error that is decidable without a daemon, on every test run, so
the gap between "written" and "run" stops widening.

    pytest infra/tests
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

yaml = pytest.importorskip("yaml")

COMPOSE = ROOT / "infra/docker/docker-compose.yml"
compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
SERVICES: dict = compose["services"]
VOLUMES: dict = compose.get("volumes") or {}

# Services we do not build: their images come from a registry and their
# internals are not ours to check.
THIRD_PARTY = {name for name, spec in SERVICES.items() if spec.get("image")}
OURS = {name: spec for name, spec in SERVICES.items() if name not in THIRD_PARTY}


def command_of(spec: dict) -> list[str]:
    return spec.get("command") or []


# ----------------------------------------------------------------- the build


def test_every_dockerfile_referenced_actually_exists():
    for name, spec in OURS.items():
        dockerfile = ROOT / spec["build"]["dockerfile"]
        assert dockerfile.exists(), f"{name} builds from a missing {dockerfile}"


def test_every_module_a_service_runs_exists():
    """`python -m services.foo.worker` with no worker.py is a container that
    exits immediately and a stack that looks up until you read the logs."""
    for name, spec in OURS.items():
        command = command_of(spec)
        if command[:2] != ["python", "-m"]:
            continue
        module = command[2]
        path = ROOT / (module.replace(".", "/") + ".py")
        assert path.exists(), f"{name} runs {module}, which is not a file"


def test_every_asgi_app_a_service_serves_exists():
    for name, spec in OURS.items():
        command = command_of(spec)
        if not command or command[0] != "uvicorn":
            continue
        module, _, attribute = command[1].partition(":")
        path = ROOT / (module.replace(".", "/") + ".py")
        assert path.exists(), f"{name} serves {module}, which is not a file"
        assert re.search(rf"^{attribute} = FastAPI", path.read_text(encoding="utf-8"), re.M), \
            f"{name} serves {command[1]}, but {path.name} defines no {attribute}"


def test_a_dockerfile_copies_the_package_its_command_runs():
    """The image is built from a copy list, not from the whole repo. A service
    whose command imports a package the Dockerfile never copied fails at
    import time, inside a container, at three in the morning."""
    for name, spec in OURS.items():
        command = command_of(spec)
        module = (command[2] if command[:2] == ["python", "-m"]
                  else command[1].split(":")[0] if command and command[0] == "uvicorn"
                  else None)
        if module is None:
            continue

        package = "/".join(module.split(".")[:2])          # services/crawl
        dockerfile = (ROOT / spec["build"]["dockerfile"]).read_text(encoding="utf-8")
        assert f"COPY {package}/" in dockerfile, \
            f"{name} runs {module} but {spec['build']['dockerfile']} never copies {package}/"


def test_every_python_image_carries_shared():
    """Everything of ours imports `shared` — contracts, the store, the bus. An
    image built without it fails on the first import, and the copy list is easy
    to write for a new service by pattern-matching an old one and missing a
    line."""
    for name, spec in OURS.items():
        dockerfile_path = spec["build"]["dockerfile"]
        if "gateway" in dockerfile_path:
            continue                                        # PHP; no shared/
        dockerfile = (ROOT / dockerfile_path).read_text(encoding="utf-8")
        assert "COPY shared/" in dockerfile, f"{name}'s image has no shared/"


def test_every_image_says_what_to_run_when_compose_does_not():
    """Two services take their command from the image. An image with no CMD
    starts and exits, which compose reports as "exited (0)" — success."""
    for name, spec in OURS.items():
        if command_of(spec):
            continue
        dockerfile = (ROOT / spec["build"]["dockerfile"]).read_text(encoding="utf-8")
        assert re.search(r"^(CMD|ENTRYPOINT)", dockerfile, re.M), \
            f"{name} has no command and its image has no CMD"


# ------------------------------------------------------------------ the wiring


HOSTNAME = re.compile(r"https?://([a-z0-9-]+):(\d+)")


def internal_urls() -> list[tuple[str, str, str, int]]:
    """Every http url in the compose environment that names a compose host."""
    found = []
    for name, spec in SERVICES.items():
        for key, value in (spec.get("environment") or {}).items():
            match = HOSTNAME.match(str(value))
            if match:
                found.append((name, key, match.group(1), int(match.group(2))))
    return found


def test_every_service_url_points_at_a_service_that_exists():
    for service, key, host, _ in internal_urls():
        assert host in SERVICES, f"{service}.{key} points at {host!r}, which is not a service"


def test_every_service_url_points_at_the_port_that_service_listens_on():
    """Right hostname, wrong port is the failure that looks like a network
    problem for an hour."""
    for service, key, host, port in internal_urls():
        command = command_of(SERVICES[host])
        if "--port" not in command:
            continue
        listening = int(command[command.index("--port") + 1])
        assert port == listening, \
            f"{service}.{key} calls {host}:{port}, but {host} listens on {listening}"


def test_a_url_never_points_at_a_worker():
    """Workers consume from the broker and serve nothing. Pointing an HTTP
    client at one is a connection refused that reads like the service is down."""
    for service, key, host, _ in internal_urls():
        assert not host.endswith("-worker"), \
            f"{service}.{key} points at {host}, which serves no HTTP"


def test_every_depends_on_names_a_real_service():
    for name, spec in SERVICES.items():
        for dependency in (spec.get("depends_on") or {}):
            assert dependency in SERVICES, f"{name} depends on {dependency!r}, which does not exist"


def test_every_named_volume_used_is_declared():
    for name, spec in SERVICES.items():
        for mount in (spec.get("volumes") or []):
            source = str(mount).split(":")[0]
            if source.startswith((".", "/")):
                continue                                    # a bind mount
            assert source in VOLUMES, f"{name} mounts {source!r}, which is not declared"


def test_no_two_services_claim_the_same_host_port():
    seen: dict[str, str] = {}
    for name, spec in SERVICES.items():
        for mapping in (spec.get("ports") or []):
            host_port = str(mapping).rsplit(":", 1)[0]
            assert host_port not in seen, \
                f"{name} and {seen[host_port]} both publish {host_port}"
            seen[host_port] = name


# --------------------------------------------------------------- the coverage


def test_every_service_with_a_worker_also_has_its_api_and_the_other_way_round():
    """Both doors or neither. A service reachable only over HTTP silently drops
    the work the orchestrator sends it, and one reachable only over the bus
    cannot be started by hand."""
    apis = {name[:-4] for name in OURS if name.endswith("-api")}
    workers = {name[:-7] for name in OURS if name.endswith("-worker")}
    # The gateway is HTTP-only by design; the relay and the scheduler are
    # neither, and are named for what they are rather than as workers.
    assert apis - workers == set(), f"no worker for: {sorted(apis - workers)}"
    assert workers - apis == set(), f"no api for: {sorted(workers - apis)}"


def test_every_service_package_in_the_repo_is_in_compose():
    """A service that exists in the repo and not here is one nobody deploys."""
    packages = {
        path.parent.name for path in (ROOT / "services").glob("*/api.py")
    }
    named = {name[:-4].replace("-", "_") for name in OURS if name.endswith("-api")}
    # The compose names are shortened where the package name is long.
    aliases = {"links": "internal_links"}
    named = {aliases.get(name, name) for name in named}

    missing = packages - named
    assert missing == set(), f"services with an api.py and no compose service: {sorted(missing)}"


def test_the_gateway_knows_where_every_service_it_calls_lives():
    """The gateway's config has a default hostname per service. A default that
    does not match the compose service name works only until someone renames
    one, and then fails as a 503 with nothing to say why."""
    config = (ROOT / "apps/api-gateway/config/services.php").read_text(encoding="utf-8")
    defaults = dict(re.findall(r"env\('(\w+_URL)',\s*'http://([a-z0-9-]+):\d+'\)", config))

    gateway = SERVICES["api-gateway"].get("environment") or {}
    for key, host in defaults.items():
        if key in gateway:
            continue                                        # explicitly set, checked above
        assert host in SERVICES, \
            f"the gateway falls back to {host!r} for {key}, and compose has no such service"


def test_the_database_is_migrated_by_something_that_runs_on_every_start():
    """A stack whose schema is only ever created once cannot receive migration
    fourteen."""
    applies = [
        name for name, spec in SERVICES.items()
        if "migrate.sh" in " ".join(command_of(spec))
    ]
    assert applies, "no service runs infra/db/migrate.sh"


def test_the_migrations_are_not_also_wired_into_the_first_boot_hook():
    """Postgres applies /docker-entrypoint-initdb.d once, on an empty volume,
    and records nothing. With both mechanisms in place the migrate service
    finds an empty schema_migrations against a full schema and fails on the
    first CREATE TABLE — so the two are mutually exclusive, not belt and
    braces."""
    for name, spec in SERVICES.items():
        for mount in (spec.get("volumes") or []):
            assert "docker-entrypoint-initdb.d" not in str(mount), \
                f"{name} mounts the migrations into the first-boot hook as well"


def test_everything_that_reads_the_database_waits_for_the_migration():
    """Racing the schema is a service that starts, fails on a missing table,
    and gets restarted by compose until the migration happens to finish — which
    looks like flakiness rather than an ordering bug."""
    for name, spec in SERVICES.items():
        environment = spec.get("environment") or {}
        needs_db = any("postgresql://" in str(v) for v in environment.values())
        if not needs_db or name == "migrate":
            continue

        waits = (spec.get("depends_on") or {}).get("migrate", {})
        assert waits.get("condition") == "service_completed_successfully", \
            f"{name} reads the database without waiting for the migration"


# ------------------------------------------------------- the outside world


PUBLISHED = re.compile(r"^(?:127\.0\.0\.1:)?(\d+):\d+$")
LOOPBACK = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d+)")


def published_ports() -> set[int]:
    ports = set()
    for spec in SERVICES.values():
        for mapping in (spec.get("ports") or []):
            match = PUBLISHED.match(str(mapping))
            if match:
                ports.add(int(match.group(1)))
    return ports


def test_a_url_meant_for_a_human_points_at_a_port_the_stack_publishes():
    """PUBLIC_BASE_URL is the one setting whose value leaves the network: it is
    what a webhook payload tells someone to click. Pointing it at a port
    nothing publishes produces links that are dead for every recipient, and
    nothing inside the stack ever notices, because nothing inside the stack
    follows them.

    Found exactly that way: it defaulted to :8080 while the gateway publishes
    :8000.
    """
    ports = published_ports()
    for name, spec in SERVICES.items():
        for key, value in (spec.get("environment") or {}).items():
            match = LOOPBACK.search(str(value))
            if not match:
                continue
            port = int(match.group(1))
            assert port in ports, \
                f"{name}.{key} sends people to localhost:{port}, which nothing publishes"


REQUIRED = re.compile(r"""os\.environ\[["'](\w+)["']\]""")


def test_every_setting_a_service_cannot_start_without_is_set():
    """`os.environ["X"]` with no X is a KeyError on the first line of a
    container that then restarts forever."""
    for name, spec in OURS.items():
        command = command_of(spec)
        module = (command[2] if command[:2] == ["python", "-m"]
                  else command[1].split(":")[0] if command and command[0] == "uvicorn"
                  else None)
        if module is None:
            continue

        source = ROOT / (module.replace(".", "/") + ".py")
        required = set(REQUIRED.findall(source.read_text(encoding="utf-8")))
        provided = set(spec.get("environment") or {})
        assert required <= provided, \
            f"{name} runs {module}, which needs {sorted(required - provided)}"


def test_the_migration_service_can_reach_the_files_it_runs():
    """The command and the mount are two halves of one decision, written
    fifteen lines apart."""
    migrate = SERVICES["migrate"]
    script = command_of(migrate)[-1]                        # /db/migrate.sh
    mounted = [str(m).split(":")[1] for m in (migrate.get("volumes") or [])]

    assert any(script.startswith(f"{point}/") for point in mounted), \
        f"migrate runs {script}, which none of {mounted} provides"

    source = ROOT / "infra/db" / Path(script).name
    assert source.exists(), f"{source} does not exist in the repo"
    assert (ROOT / "infra/db/migrations").is_dir(), "there are no migrations to apply"
