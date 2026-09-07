"""Exercise actual Tk widgets, worker completion, and persistence without operator input."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from primorets.gui import App
from primorets.core import export_csv

root = Path(__file__).resolve().parents[1] / ".test-output" / "gui"
app = App(root)
app.withdraw()
app.add_demo()
app.update()
assert len(app.tree.get_children()) == 2
app.fields["latitude"].set("55")
app.fields["longitude"].set("37")
app.fields["start"].set("2026-09-07 00:00:00")
app.calculate()
deadline = time.monotonic() + 30
while app.busy and time.monotonic() < deadline:
    app.update()
    time.sleep(0.05)
assert not app.busy, "Calculation timed out"
assert len(app.points) == 1441
assert app.export_points[0]["demo"] is True
assert "УЧЕБНЫЕ" in app.summary.get()
export_csv(root / "trajectory.csv", app.export_points)
app.draw()
assert len(app.canvas.find_all()) > 10
app.open_session()
window = app.session_window
window.withdraw()
window.visible_start()
window.start()
window.model.advance(60)
window.render()
assert window.model.commands_sent > 0
assert window.model.link
assert len(window.canvas.find_all()) > 10
window.disturb()
assert not window.model.link
window.model.advance(10)
assert window.model.link
window.fault.set(True)
window.change_fault()
assert not window.model.link
window.stop()
assert not window.model.running
window.reset()
assert window.model.elapsed == 0
window.close()
app.close()
assert json.loads((root / "settings.json").read_text(encoding="utf-8"))["latitude"] == "55"
print("GUI smoke passed: catalog, filter, worker, plot, export, settings, session and faults")

