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

    def __init__(self, config):
        self.config = config

    # -- capability probe ---------------------------------------------------
    def probe(self):
        """Raise BackendUnavailable when this backend cannot run here."""
        raise NotImplementedError

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
        cmd = self._cmd("create", "--name", self.config["sandbox"])
        if mode == "clone":
            cmd.append("--clone")
        cmd += ["claude", project]
        result = self._run(*cmd)
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
