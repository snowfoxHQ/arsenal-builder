"""Sandbox backend contract: the five capabilities the runner actually needs.

sbx used to be hard-wired into runner.py. It is now just one implementation of
this contract, so the same pipeline can drive any isolated environment - or,
with the none backend (see v3.2), no isolation at all, explicitly accepted.

The contract is deliberately small:

    probe()          the backend works at all (binary present, daemon up)
    exists()         a sandbox instance is already there
    binding()        which host directory the instance is tied to
    create(host_dir) make a new instance for this host directory
    exec/copy/clear  run the agent inside, pull artifacts out, clean up

Every method may raise BackendUnavailable (setup problem -> exit 2) or
BackendError (misconfiguration -> exit 2). Nothing here parses prose: bindings
come from structured output only, and a backend that cannot report one returns
None so the caller can warn instead of pretending it checked.
"""

BACKENDS = {}


def register(cls):
    BACKENDS[cls.name] = cls
    return cls


class BackendError(Exception):
    """Configuration problem: the backend name is unknown or miswired."""


class BackendUnavailable(BackendError):
    """The backend's tooling is missing or not reachable right now."""


class SandboxBackend:
    """Base class. Subclasses set `name` and implement the methods."""

    name = "abstract"

    # False means the agent runs directly on this machine - the runner then
    # demands explicit "i_accept_no_isolation" confirmation in preflight.
    isolation = True

    def __init__(self, config):
        self.config = config

    @property
    def artifact_dir(self):
        """Where the agent writes artifacts, as seen from inside the instance."""
        return str(self.config.get("sandbox_out_dir") or "/tmp/arsenal-out").rstrip("/")

    # -- capability probe ---------------------------------------------------
    def probe(self):
        """Raise BackendUnavailable when this backend cannot run here."""
        raise NotImplementedError

    def version(self):
        """The tooling version behind this backend, or None if unknown.

        Feeds run_manifest.json - two runs are only comparable when this is
        the same, so it is captured, never parsed for behaviour.
        """
        return None

    # -- instance life cycle -------------------------------------------------
    def exists(self):
        """True when a sandbox instance already exists."""
        raise NotImplementedError

    def binding(self):
        """The host directory this instance is bound to, or None if unknown.

        None means "cannot tell" - the caller must warn, because silence here
        reads as "checked and fine".
        """
        raise NotImplementedError

    def create(self):
        """Create an instance bound to config['project_dir']. False on failure."""
        raise NotImplementedError

    def remove(self):
        """Delete the existing instance. Best-effort; may log."""
        raise NotImplementedError

    # -- inside the instance -------------------------------------------------
    def in_sandbox_path(self, host_path):
        """Translate a host path into how the instance sees it."""
        raise NotImplementedError

    def clear_dir(self, sandbox_dir):
        """Delete a directory inside the instance. Best-effort."""
        raise NotImplementedError

    def exec_agent(self, args, env_file, log_path):
        """Run the agent command inside the instance, teeing output to log_path.

        `args` is the command vector (e.g. ['claude', '-p', prompt, ...]);
        `env_file` is a 0600 file with the key - implementations must hand it
        to the tool in a way that never puts the key into argv.
        Returns the agent's exit code.
        """
        raise NotImplementedError

    def exec_capture(self, args):
        """Run a short command inside the instance, capture (rc, stdout)."""
        raise NotImplementedError

    # -- artifact recovery ----------------------------------------------------
    def fetch_dir(self, sandbox_dir, local_dir):
        """Copy a directory out of the instance. False when the copy failed."""
        raise NotImplementedError

    def fetch_file(self, sandbox_dir, name, local_path):
        """Copy one file out of the instance. False when the copy failed."""
        raise NotImplementedError


def _first(config, *keys, default=None):
    for key in keys:
        value = config.get(key)
        if value:
            return value
    return default


def get_backend(config):
    """Instantiate the configured backend. Raises BackendError on a bad name."""
    name = str(config.get("backend") or "sbx").strip().lower()
    cls = BACKENDS.get(name)
    if cls is None:
        raise BackendError(
            "unknown sandbox backend: %r (installed: %s)"
            % (name, ", ".join(sorted(BACKENDS)))
        )
    return cls(config)


@register
class SbxBackend(SandboxBackend):
    """Docker sandbox CLI (`sbx`). The reference implementation.

    One instance binds one host directory for life, and it reports that
    binding through `sbx ls --format json` - the property the runner's
    wrong-tree check is built on.
    """

    name = "sbx"

    def _bin(self):
        binary = self.config.get("sbx_bin") or "sbx"
        if isinstance(binary, (list, tuple)):
            return list(binary)
        return [binary]

    def _cmd(self, *args):
        return self._bin() + list(args)

    def _run(self, *args):
        import subprocess

        return subprocess.run(
            self._cmd(*args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    # -- capability probe ----------------------------------------------------
    def probe(self):
        import os
        import shutil

        binary = self._bin()
        probe = binary[0]
        if os.path.isabs(probe) or os.sep in probe or "/" in probe:
            if not os.path.exists(probe):
                raise BackendUnavailable("sbx binary not found: %s" % probe)
        elif shutil.which(probe) is None:
            raise BackendUnavailable(
                "sbx not on PATH. Install the standalone sbx CLI (MSI) and "
                "reopen the shell."
            )

    def version(self):
        result = self._run("--version")
        return result.stdout.strip().splitlines()[0] if result.returncode == 0 else None

    # -- instance life cycle ---------------------------------------------------
    def _listing(self):
        """Structured `sbx ls`, so bindings can be compared, not guessed."""
        import json

        try:
            result = self._run("ls", "--format", "json")
            data = json.loads(result.stdout)
        except Exception:  # noqa: BLE001 - unreadable listing must not crash a run
            return []
        return data if isinstance(data, list) else []

    def _entry(self):
        name = self.config["sandbox"]
        for entry in self._listing():
            if isinstance(entry, dict) and entry.get("name") == name:
                return entry
        return None

    def exists(self):
        return self._entry() is not None

    def binding(self):
        entry = self._entry()
        if entry is None:
            return None
        workspaces = entry.get("workspaces") or []
        return str(workspaces[0]) if workspaces else None

    def create(self):
        import os

        mode = str(self.config["workspace_mode"]).lower()
        project = os.path.abspath(self.config["project_dir"])
        # _run() prepends the binary itself: pass arguments only. Passing a
        # list built with _cmd() produced `sbx sbx create` (2026-11-05) - the
        # fake accepted it, the real CLI did not.
        args = ["create", "--name", self.config["sandbox"]]
        if mode == "clone":
            args.append("--clone")
        args += ["claude", project]
        result = self._run(*args)
        if result.stdout.strip():
            print(result.stdout, end="", flush=True)
        if result.returncode != 0:
            print("[arsenal] sbx create failed with exit code %d" % result.returncode,
                  flush=True)
            return False
        return True

    def remove(self):
        print("[arsenal] removing sandbox %s" % self.config["sandbox"], flush=True)
        # --force is mandatory: `sbx rm` otherwise waits for an interactive
        # confirmation, which deadlocks a double-clicked .bat.
        self._run("rm", "--force", self.config["sandbox"])

    # -- inside the instance ---------------------------------------------------
    def in_sandbox_path(self, host_path):
        import os

        path = os.path.abspath(host_path).replace("\\", "/")
        if len(path) > 1 and path[1] == ":":
            return "/" + path[0].lower() + path[2:]
        return path

    def exec_capture(self, args):
        result = self._run("exec", self.config["sandbox"], *args)
        return result.returncode, result.stdout

    def clear_dir(self, sandbox_dir):
        result = self._run("exec", self.config["sandbox"], "rm", "-rf", sandbox_dir)
        return result.returncode == 0

    def exec_agent(self, args, env_file, log_path):
        import subprocess
        import sys
        import time

        cmd = self._cmd(
            "exec", "--env-file", env_file, self.config["sandbox"]
        ) + list(args)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,  # never wait on a console nobody types into
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",  # a stray GBK byte must never crash the pipeline
        )
        started = time.time()
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write(
                "[arsenal] agent output for sandbox %s\n" % self.config["sandbox"]
            )
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_file.write(line)
                log_file.flush()
            proc.wait()
            log_file.write("\n[arsenal] agent exit code %d\n" % proc.returncode)
        print(
            "[arsenal] agent finished in %ds with exit code %d"
            % (int(time.time() - started), proc.returncode),
            flush=True,
        )
        return proc.returncode

    # -- artifact recovery ------------------------------------------------------
    def fetch_dir(self, sandbox_dir, local_dir):
        remote = "%s:%s" % (self.config["sandbox"], sandbox_dir)
        result = self._run("cp", remote, local_dir)
        return result.returncode == 0

    def fetch_file(self, sandbox_dir, name, local_path):
        import os

        source = "%s:%s/%s" % (self.config["sandbox"], sandbox_dir, name)
        result = self._run("cp", source, local_path)
        return result.returncode == 0 and os.path.isfile(local_path)


BINDING_LABEL = "arsenal.project"


@register
class DockerBackend(SandboxBackend):
    """Plain Docker containers. Bind-mount only, and only with consent.

    sbx clones the project into a private microVM; a plain container has no
    equivalent without a git remote inside the image, so this backend mounts
    the host directory read-write - which hands the agent the host directory.
    That is exactly the risk `i_accept_bind_mount_risk` exists for, so this
    backend refuses to run without it (checked in runner preflight).

    The binding is recorded as a label at create time and read back through
    `docker inspect` - structured, so the wrong-tree check keeps working.
    """

    name = "docker"

    def _bin(self):
        binary = self.config.get("docker_bin") or "docker"
        if isinstance(binary, (list, tuple)):
            return list(binary)
        return [binary]

    def _run(self, *args):
        import subprocess

        return subprocess.run(
            self._bin() + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def probe(self):
        import shutil

        if shutil.which(self._bin()[0]) is None:
            raise BackendUnavailable(
                "docker not on PATH. Install Docker Desktop (or an engine) "
                "and reopen the shell."
            )
        result = self._run("info")
        if result.returncode != 0:
            raise BackendUnavailable(
                "docker daemon not reachable - is Docker Desktop running?"
            )
        if not str(self.config.get("docker_image") or "").strip():
            raise BackendError(
                'backend "docker" needs "docker_image" in config: an image that '
                "has the claude CLI and a git client inside."
            )

    def version(self):
        result = self._run("--version")
        return result.stdout.strip().splitlines()[0] if result.returncode == 0 else None

    def exists(self):
        result = self._run(
            "ps", "-a", "--filter",
            "name=^/%s$" % self.config["sandbox"],
            "--format", "{{.Names}}",
        )
        names = result.stdout.split()
        return self.config["sandbox"] in names

    def binding(self):
        result = self._run(
            "inspect",
            "--format", "{{index .Config.Labels \"%s\"}}" % BINDING_LABEL,
            self.config["sandbox"],
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value if value and "<no value>" not in value else None

    def create(self):
        import os

        project = os.path.abspath(self.config["project_dir"])
        result = self._run(
            "run", "-d",
            "--name", self.config["sandbox"],
            "--label", "%s=%s" % (BINDING_LABEL, project),
            "--mount",
            "type=bind,source=%s,target=%s" % (project, self.in_sandbox_path(project)),
            self.config["docker_image"],
            "sleep", "infinity",
        )
        if result.returncode != 0:
            print(result.stdout, end="", flush=True)
            print("[arsenal] docker create failed with exit code %d" % result.returncode,
                  flush=True)
            return False
        return True

    def remove(self):
        print("[arsenal] removing container %s" % self.config["sandbox"], flush=True)
        self._run("rm", "-f", self.config["sandbox"])

    def in_sandbox_path(self, host_path):
        return str(self.config.get("docker_workspace") or "/workspace")

    def exec_capture(self, args):
        result = self._run("exec", self.config["sandbox"], *args)
        return result.returncode, result.stdout

    def clear_dir(self, sandbox_dir):
        result = self._run("exec", self.config["sandbox"], "rm", "-rf", sandbox_dir)
        return result.returncode == 0

    def exec_agent(self, args, env_file, log_path):
        import subprocess
        import sys
        import time

        cmd = self._bin() + ["exec", "--env-file", env_file, self.config["sandbox"]] + list(args)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        started = time.time()
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write(
                "[arsenal] agent output for container %s\n" % self.config["sandbox"]
            )
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_file.write(line)
                log_file.flush()
            proc.wait()
            log_file.write("\n[arsenal] agent exit code %d\n" % proc.returncode)
        print(
            "[arsenal] agent finished in %ds with exit code %d"
            % (int(time.time() - started), proc.returncode),
            flush=True,
        )
        return proc.returncode

    def fetch_dir(self, sandbox_dir, local_dir):
        remote = "%s:%s" % (self.config["sandbox"], sandbox_dir)
        result = self._run("cp", remote, local_dir)
        return result.returncode == 0

    def fetch_file(self, sandbox_dir, name, local_path):
        import os

        source = "%s:%s/%s" % (self.config["sandbox"], sandbox_dir, name)
        result = self._run("cp", source, local_path)
        return result.returncode == 0 and os.path.isfile(local_path)


@register
class NoneBackend(SandboxBackend):
    """No isolation: the agent runs on this machine, in the project directory.

    A legitimate backend, not an accident - for quick iteration and for CI
    machines without any sandbox runtime. It changes the trust model: the
    agent can touch anything this user can, so the runner refuses to start
    without "i_accept_no_isolation": true in the config.

    sandbox_out_dir is a sandbox-side path and means nothing here; artifacts
    go to a fixed scratch directory under the system temp dir instead.
    """

    name = "none"
    isolation = False

    @property
    def artifact_dir(self):
        import os
        import tempfile

        return os.path.join(tempfile.gettempdir(), "arsenal-none-out")

    def probe(self):
        return None

    def version(self):
        return "host (no isolation)"

    def exists(self):
        return True

    def binding(self):
        import os

        return os.path.abspath(self.config["project_dir"])

    def create(self):
        print("[arsenal] none backend: no instance to create", flush=True)
        return True

    def remove(self):
        print("[arsenal] none backend: no instance to remove", flush=True)

    def in_sandbox_path(self, host_path):
        import os

        return os.path.abspath(host_path)

    def _env_from_file(self, env_file):
        import os

        env = dict(os.environ)
        try:
            with open(env_file, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, value = line.split("=", 1)
                        env[key] = value
        except OSError:
            pass
        return env

    def exec_capture(self, args):
        import os
        import subprocess

        result = subprocess.run(
            args,
            cwd=self.config["project_dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode, result.stdout

    def clear_dir(self, sandbox_dir):
        import shutil

        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return True

    def exec_agent(self, args, env_file, log_path):
        import os
        import subprocess
        import sys
        import time

        proc = subprocess.Popen(
            args,
            cwd=self.config["project_dir"],
            env=self._env_from_file(env_file),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        started = time.time()
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write("[arsenal] agent output (none backend, no isolation)\n")
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_file.write(line)
                log_file.flush()
            proc.wait()
            log_file.write("\n[arsenal] agent exit code %d\n" % proc.returncode)
        print(
            "[arsenal] agent finished in %ds with exit code %d"
            % (int(time.time() - started), proc.returncode),
            flush=True,
        )
        return proc.returncode

    def fetch_dir(self, sandbox_dir, local_dir):
        import os
        import shutil

        if not os.path.isdir(sandbox_dir):
            return False
        target = os.path.join(local_dir, os.path.basename(sandbox_dir.rstrip("/\\")))
        shutil.copytree(sandbox_dir, target, dirs_exist_ok=True)
        return True

    def fetch_file(self, sandbox_dir, name, local_path):
        import os
        import shutil

        source = os.path.join(sandbox_dir, name)
        if not os.path.isfile(source):
            return False
        shutil.copyfile(source, local_path)
        return os.path.isfile(local_path)
