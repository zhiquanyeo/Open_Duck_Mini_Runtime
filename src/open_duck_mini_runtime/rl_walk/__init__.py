"""RL walk loop: policy inference, gait, main run loop.

Import submodules directly (e.g. `from open_duck_mini_runtime.rl_walk.walk
import RLWalk`) — nothing here is re-exported at the package level, so
pure-logic submodules (stability_governor, control_bus, battery,
walk_defaults) stay importable without walk.py's hardware dependencies."""
