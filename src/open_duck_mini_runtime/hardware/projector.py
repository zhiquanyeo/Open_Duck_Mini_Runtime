import time

from open_duck_mini_runtime.hardware.led_controller import get_controller


class Projector:
    def __init__(self, led_counts: dict | None = None):
        self.ctrl = get_controller(led_counts=led_counts)
        self.on = False
        self.ctrl.set_projector(False)

    def switch(self):
        self.on = not self.on
        self.ctrl.set_projector(self.on)

    def stop(self):
        self.on = False
        self.ctrl.set_projector(False)


if __name__ == "__main__":
    p = Projector()
    try:
        while True:
            p.switch()
            time.sleep(1)
    finally:
        p.stop()
