"""Printfarm (fork): pieces recognised by geometry across plates, whatever the objects are called.

All meshes are generated here (boxes and an irregular tetrahedron); none come from a real farm.
"""

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from backend.app.api.routes import library
from backend.app.farm.plates.geometry import fingerprint, group, same_piece
from backend.app.farm.plates.service import parse_plates
from backend.tests.integration.test_farm_library import BAMBU, add_external, age, scan

API = "/api/v1/farm"
ROTATE_Z = "0 1 0 -1 0 0 0 0 1 40 15 0"  # 90° around z, moved: same piece


def box(dx: float, dy: float, dz: float):
    V = np.array([[x, y, z] for x in (0, dx) for y in (0, dy) for z in (0, dz)], dtype=float)
    F = np.array(
        [[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4],
         [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]]
    )
    return V, F


def tetra(mirror: bool = False):
    V = np.array([[0, 0, 0], [4, 0, 0], [0, 2, 0], [1, 1, 3]], dtype=float)
    F = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
    if mirror:
        V, F = V * [-1, 1, 1], F[:, ::-1]
    return V, F


def make_mesh_project(path: Path, plates: dict[int, list[tuple[str, tuple, str | None]]]) -> Path:
    """Plates of ``(object name, (V, F), build transform)``; each instance is its own object, as slicers save."""
    objects, items, settings, plate_xml, oid = [], [], [], [], 0
    for index, entries in plates.items():
        instances = []
        for name, (V, F), transform in entries:
            oid += 1
            verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in V)
            tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F)
            objects.append(f'<object id="{oid}" type="model"><mesh><vertices>{verts}</vertices>'
                           f"<triangles>{tris}</triangles></mesh></object>")
            items.append(f'<item objectid="{oid}"' + (f' transform="{transform}"' if transform else "") + "/>")
            settings.append(f'<object id="{oid}"><metadata key="name" value="{name}"/>'
                            '<metadata key="extruder" value="1"/></object>')
            instances.append(f'<model_instance><metadata key="object_id" value="{oid}"/></model_instance>')
        plate_xml.append(f'<plate><metadata key="plater_id" value="{index}"/>{"".join(instances)}</plate>')
    meta = "".join(f'<metadata name="{k}">{v}</metadata>' for k, v in BAMBU.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "3D/3dmodel.model",
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter">'
            f"{meta}<resources>{''.join(objects)}</resources><build>{''.join(items)}</build></model>",
        )
        zf.writestr("Metadata/model_settings.config", f"<config>{''.join(settings)}{''.join(plate_xml)}</config>")
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps({"printer_model": "Bambu Lab P1S", "filament_type": ["PLA"], "nozzle_diameter": ["0.4"]}),
        )
    return age(path)


def test_fingerprint_ignores_position_and_rotation_not_size_or_mirror():
    V, F = box(10, 20, 5)
    A = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], dtype=float)
    base = fingerprint(V, F)
    assert base[:2] == pytest.approx([1000, 2 * (200 + 50 + 100)])
    assert same_piece(base, fingerprint(V @ A + [40, 15, 3], F))
    assert not same_piece(base, fingerprint(*box(10, 20, 5.1)))  # 2% bigger: another piece

    left, right = fingerprint(*tetra()), fingerprint(*tetra(mirror=True))
    assert left[:5] == pytest.approx(right[:5])
    assert {left[5], right[5]} == {1.0, -1.0}
    assert not same_piece(left, right)
    assert base[5] == 0  # a box is its own mirror image

    assert group([base, left, fingerprint(V + 7, F), right]) == [[0, 2], [1], [3]]


def test_parse_plates_measures_each_object_name(tmp_path):
    hook, lid = box(10, 20, 5), box(30, 30, 2)
    path = make_mesh_project(
        tmp_path / "p.3mf",
        {1: [("Hook", hook, None), ("Hook", hook, "1 0 0 0 1 0 0 0 1 20 0 0")], 2: [("Lid", lid, None)]},
    )
    plates, _ = parse_plates(path)
    assert [(p.index, p.objects) for p in plates] == [(1, {"Hook": 2}), (2, {"Lid": 1})]
    assert plates[0].fingerprints["Hook"][0] == pytest.approx(1000)
    assert plates[1].fingerprints["Lid"][0] == pytest.approx(1800)


@pytest.fixture(autouse=True)
def allow_test_external_root(monkeypatch, tmp_path):
    monkeypatch.setattr(library.app_settings, "external_roots", str(tmp_path))


@pytest.mark.integration
async def test_link_a_piece_once_for_all_its_plates_and_learn_it(async_client, tmp_path):
    hook, lid = box(10, 20, 5), box(30, 30, 2)
    nas = tmp_path / "nas"
    make_mesh_project(
        nas / "Hook" / "hook.3mf",
        {1: [("Hook", hook, None)] * 2, 2: [("Hook renamed", hook, ROTATE_Z), ("Lid", lid, None)]},
    )
    folder = await add_external(async_client, nas)
    await async_client.put(f"{API}/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)

    pieces = (await async_client.get(f"{API}/pieces")).json()
    by_name = {p["names"][0]: p for p in pieces}
    assert sorted(by_name["Hook"]["names"]) == ["Hook", "Hook renamed"]
    assert (by_name["Hook"]["objects"], by_name["Hook"]["plates"], by_name["Hook"]["unlinked"]) == (3, 2, 2)
    assert (by_name["Lid"]["objects"], by_name["Lid"]["plates"]) == (1, 1)

    gancio = (await async_client.post(f"{API}/shapes", json={"name": "Gancio"})).json()
    coperchio = (await async_client.post(f"{API}/shapes", json={"name": "Coperchio"})).json()
    r = await async_client.put(f"{API}/pieces", json={"yield_ids": by_name["Hook"]["yield_ids"], "shape_id": gancio["id"]})
    assert r.json() == {"linked": 2, "plates_confirmed": 1}  # plate 2 still has the lid
    r = await async_client.put(f"{API}/pieces", json={"yield_ids": by_name["Lid"]["yield_ids"], "shape_id": coperchio["id"]})
    assert r.json() == {"linked": 1, "plates_confirmed": 1}
    bad = await async_client.put(f"{API}/pieces", json={"yield_ids": by_name["Lid"]["yield_ids"], "shape_id": 999})
    assert bad.status_code == 422
    assert all(p["unlinked"] == 0 for p in (await async_client.get(f"{API}/pieces")).json())

    # A new project with the same hook under another name: linked and confirmed on its own.
    make_mesh_project(nas / "Other" / "other.3mf", {1: [("Gancio v2 (1)", hook, ROTATE_Z)] * 3})
    await scan(async_client, folder)
    files = {Path(f["path"]).name: f["library_file_id"] for f in (await async_client.get(f"{API}/library/files")).json()}
    plates = (await async_client.get(f"{API}/library/files/{files['other.3mf']}/plates")).json()
    assert [(p["status"], [(y["object_name"], y["quantity"], y["shape_id"]) for y in p["yields"]]) for p in plates] == [
        ("confirmed", [("Gancio v2 (1)", 3, gancio["id"])])
    ]


@pytest.mark.integration
async def test_product_pieces_mapped_from_the_product_page(async_client, tmp_path):
    """Only the pieces of the product's folders; one save links the shape, the component and the part."""
    hook, lid, spring = box(10, 20, 5), box(30, 30, 2), tetra()
    nas = tmp_path / "nas"
    make_mesh_project(nas / "Kit" / "kit.3mf", {1: [("Hook", hook, None)] * 2 + [("Lid", lid, None)]})
    make_mesh_project(nas / "Other" / "other.3mf", {1: [("Spring", spring, None)]})
    folder = await add_external(async_client, nas)
    await async_client.put(f"{API}/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)
    files = {
        Path(f["path"]).name: f["library_file_id"] for f in (await async_client.get(f"{API}/library/files")).json()
    }
    kit_folder = (await async_client.get(f"/api/v1/library/files/{files['kit.3mf']}")).json()["folder_id"]

    # Imported from Seller Hub as a whole: a component without a shape, already in the bill of materials.
    whole = (await async_client.post(f"{API}/components", json={"name": "Coperchio kit"})).json()
    r = await async_client.post(
        f"{API}/products", json={"name": "Kit", "parts": [{"component_code": whole["code"], "quantity": 1}]}
    )
    pid = r.json()["project_id"]
    r = await async_client.put(f"{API}/folders/{kit_folder}/projects", json={"project_ids": [pid]})
    assert r.status_code == 200, r.text

    body = (await async_client.get(f"{API}/products/{pid}/pieces")).json()
    by_name = {p["names"][0]: p for p in body["pieces"]}
    assert sorted(by_name) == ["Hook", "Lid"]  # the spring is in another folder
    assert body["unshaped_parts"] == [{"code": whole["code"], "name": "Coperchio kit", "quantity": 1}]
    assert (by_name["Hook"]["quantity"], by_name["Hook"]["shape_id"], by_name["Hook"]["plates"]) == (None, None, 1)

    r = await async_client.put(
        f"{API}/products/{pid}/pieces",
        json={"yield_ids": by_name["Hook"]["yield_ids"], "quantity": 2, "shape_name": "Gancio", "color": "Nero"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["plates_confirmed"] == 0  # the lid is still unknown
    hook_code = r.json()["component_code"]
    # The lid takes the existing component: same code, so Seller Hub's stock stays where it is.
    r = await async_client.put(
        f"{API}/products/{pid}/pieces",
        json={
            "yield_ids": by_name["Lid"]["yield_ids"],
            "quantity": 1,
            "shape_name": "Coperchio",
            "component_code": whole["code"],
        },
    )
    assert (r.json()["component_code"], r.json()["plates_confirmed"]) == (whole["code"], 1)

    body = (await async_client.get(f"{API}/products/{pid}/pieces")).json()
    assert body["unshaped_parts"] == []
    assert body["default_color"] == "Nero"
    got = {p["names"][0]: (p["shape"], p["quantity"], p["component"]["code"]) for p in body["pieces"]}
    assert got == {"Hook": ("Gancio", 2, hook_code), "Lid": ("Coperchio", 1, whole["code"])}
    product = next(p for p in (await async_client.get(f"{API}/products")).json()["products"] if p["project_id"] == pid)
    assert sorted((part["component_code"], part["quantity"]) for part in product["parts"]) == sorted(
        [(hook_code, 2), (whole["code"], 1)]
    )

    # The piece alone, in its colour; the same picture next time.
    url = f"{API}/pieces/{by_name['Hook']['image_yield_id']}/image?color=111111"
    first = await async_client.get(url)
    assert (first.status_code, first.content[:4]) == (200, b"\x89PNG")
    assert (await async_client.get(url)).content == first.content

    # 0: not part of the product any more.
    r = await async_client.put(
        f"{API}/products/{pid}/pieces", json={"yield_ids": by_name["Hook"]["yield_ids"], "quantity": 0}
    )
    assert r.status_code == 200
    product = next(p for p in (await async_client.get(f"{API}/products")).json()["products"] if p["project_id"] == pid)
    assert [part["component_code"] for part in product["parts"]] == [whole["code"]]
    assert (await async_client.get(f"{API}/products/9999/pieces")).status_code == 404


async def test_product_leaves_a_3mf_out(async_client, tmp_path):
    """An excluded 3MF keeps its plates hidden and unchanged; including it again brings them back."""
    nas = tmp_path / "nas"
    make_mesh_project(nas / "Kit" / "kit.3mf", {1: [("Hook", box(10, 20, 5), None)]})
    old = make_mesh_project(nas / "Kit" / "old.3mf", {1: [("Lid", box(30, 30, 2), None)]})
    folder = await add_external(async_client, nas)
    await async_client.put(f"{API}/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)
    files = {
        Path(f["path"]).name: f["library_file_id"] for f in (await async_client.get(f"{API}/library/files")).json()
    }
    kit_folder = (await async_client.get(f"/api/v1/library/files/{files['kit.3mf']}")).json()["folder_id"]
    pid = (await async_client.post(f"{API}/products", json={"name": "Kit"})).json()["project_id"]
    await async_client.put(f"{API}/folders/{kit_folder}/projects", json={"project_ids": [pid]})
    lid = next(
        p for p in (await async_client.get(f"{API}/products/{pid}/pieces")).json()["pieces"] if p["names"] == ["Lid"]
    )
    r = await async_client.put(
        f"{API}/products/{pid}/pieces", json={"yield_ids": lid["yield_ids"], "quantity": 1, "shape_name": "Coperchio"}
    )
    lid_code = r.json()["component_code"]
    lid_plates = [
        p["id"] for p in (await async_client.get(f"{API}/plates")).json() if p["library_file_id"] == files["old.3mf"]
    ]

    r = await async_client.put(f"{API}/products/{pid}/files/{files['old.3mf']}", json={"excluded": True})
    assert r.status_code == 200, r.text
    view = (await async_client.get(f"{API}/products/{pid}/view")).json()
    assert {f["filename"]: f["excluded"] for f in view["files"]} == {"kit.3mf": False, "old.3mf": True}
    assert next(p for p in view["parts"] if p["code"] == lid_code)["plates"] == []
    pieces = (await async_client.get(f"{API}/products/{pid}/pieces")).json()["pieces"]
    assert [p["names"] for p in pieces] == [["Hook"]]
    assert all(p["library_file_id"] != files["old.3mf"] for p in (await async_client.get(f"{API}/plates")).json())
    assert all(p["names"] != ["Lid"] for p in (await async_client.get(f"{API}/pieces")).json())
    assert (await async_client.get(f"{API}/library/files/{files['old.3mf']}/plates")).json() == []
    r = await async_client.post(f"{API}/jobs", json={"jobs": [{"component_code": lid_code, "plate_ids": lid_plates}]})
    assert r.status_code == 422

    # Saved again while excluded: not read (no new plates, no geometry).
    make_mesh_project(old, {1: [("Spring", tetra(), None)]})
    await scan(async_client, folder)

    # Another product using the same folder still sees it.
    other = (await async_client.post(f"{API}/products", json={"name": "Kit bis"})).json()["project_id"]
    await async_client.put(f"{API}/folders/{kit_folder}/projects", json={"project_ids": [pid, other]})
    assert any(p["library_file_id"] == files["old.3mf"] for p in (await async_client.get(f"{API}/plates")).json())
    await async_client.put(f"{API}/folders/{kit_folder}/projects", json={"project_ids": [pid]})

    r = await async_client.put(f"{API}/products/{pid}/files/{files['old.3mf']}", json={"excluded": False})
    assert r.status_code == 200
    back = [p for p in (await async_client.get(f"{API}/plates")).json() if p["library_file_id"] == files["old.3mf"]]
    assert [p["id"] for p in back] == lid_plates  # kept, as they were before the exclusion
    assert [y["object_name"] for y in back[0]["yields"]] == ["Lid"]
    assert (await async_client.put(f"{API}/products/{pid}/files/999999", json={"excluded": True})).status_code == 404


async def test_product_printed_as_a_set(async_client, tmp_path):
    """A set has one component; plates yield complete sets (4 tops + 3 bottoms = 3 sets)."""
    top, bottom = box(10, 20, 5), box(30, 30, 2)
    nas = tmp_path / "nas"
    make_mesh_project(
        nas / "Set" / "set.3mf",
        {
            1: [("Top", top, None)] * 4 + [("Bottom", bottom, None)] * 4,
            2: [("Top", top, None)] * 4 + [("Bottom", bottom, None)] * 3,
        },
    )
    folder = await add_external(async_client, nas)
    await async_client.put(f"{API}/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)
    file_id = (await async_client.get(f"{API}/library/files")).json()[0]["library_file_id"]
    set_folder = (await async_client.get(f"/api/v1/library/files/{file_id}")).json()["folder_id"]
    pid = (await async_client.post(f"{API}/products", json={"name": "Tartufi"})).json()["project_id"]
    await async_client.put(f"{API}/folders/{set_folder}/projects", json={"project_ids": [pid]})

    r = await async_client.put(f"{API}/products/{pid}/kind", json={"kind": "set"})
    assert r.json() == {"project_id": pid, "kind": "set"}
    pieces = {p["names"][0]: p for p in (await async_client.get(f"{API}/products/{pid}/pieces")).json()["pieces"]}
    codes = set()
    for name in ("Top", "Bottom"):
        r = await async_client.put(
            f"{API}/products/{pid}/pieces",
            json={"yield_ids": pieces[name]["yield_ids"], "quantity": 1, "shape_name": name, "color": "Nero"},
        )
        codes.add(r.json()["component_code"])
    assert len(codes) == 1  # one component: the set
    code = codes.pop()

    body = (await async_client.get(f"{API}/products/{pid}/pieces")).json()
    assert (body["kind"], body["set"]["code"], body["unshaped_parts"]) == ("set", code, [])
    assert {p["names"][0]: (p["quantity"], p["component"]["code"]) for p in body["pieces"]} == {
        "Top": (1, code),
        "Bottom": (1, code),
    }
    view = (await async_client.get(f"{API}/products/{pid}/view")).json()
    assert (view["name"], view["has_cover"]) == ("Tartufi", False)
    assert [(p["code"], p["quantity"], sorted(x["yield"] for x in p["plates"])) for p in view["parts"]] == [
        (code, 1, [3, 4])
    ]
    assert sorted(r["quantity"] for r in view["parts"][0]["recipe"]) == [1, 1]
    contract = next(p for p in (await async_client.get(f"{API}/products")).json()["products"] if p["project_id"] == pid)
    assert contract["kind"] == "set"
    assert sorted(x["yield"] for x in contract["parts"][0]["plates"]) == [3, 4]

    # The set's picture: both pieces side by side, in its colour; the same picture next time.
    first = await async_client.get(f"{API}/components/{code}/image")
    assert (first.status_code, first.content[:4]) == (200, b"\x89PNG")
    assert Image.open(io.BytesIO(first.content)).size == (512, 256)
    assert (await async_client.get(f"{API}/components/{code}/image")).content == first.content
    # The set takes no second component.
    other = (await async_client.post(f"{API}/components", json={"name": "Scatola"})).json()["code"]
    assert (await async_client.put(f"{API}/products/{pid}/parts/{other}", json={"quantity": 1})).status_code == 409
    assert (await async_client.get(f"{API}/components/{other}/image")).status_code == 404  # nothing to draw

    # Two tops per set: plate 1 gives 2 sets, plate 2 gives 2.
    r = await async_client.put(
        f"{API}/products/{pid}/pieces", json={"yield_ids": pieces["Top"]["yield_ids"], "quantity": 2}
    )
    view = (await async_client.get(f"{API}/products/{pid}/view")).json()
    assert sorted(x["yield"] for x in view["parts"][0]["plates"]) == [2, 2]
    r = await async_client.put(f"{API}/products/{pid}/kind", json={"kind": "kit"})
    assert r.status_code == 409  # two pieces in the set


async def test_a_product_with_left_and_right_parts_becomes_one_set(async_client):
    shapes = [(await async_client.post(f"{API}/shapes", json={"name": n})).json()["id"] for n in ("SX", "DX")]
    comps = [
        (await async_client.post(f"{API}/components", json={"shape_id": s, "color": "Nero"})).json()["code"]
        for s in shapes
    ]
    r = await async_client.post(
        f"{API}/products",
        json={"name": "Coppia", "parts": [{"component_code": comps[0]}, {"component_code": comps[1]}]},
    )
    pid = r.json()["project_id"]
    other = await async_client.post(
        f"{API}/products", json={"name": "Solo DX", "parts": [{"component_code": comps[1]}]}
    )
    assert other.status_code == 200

    assert (await async_client.put(f"{API}/products/{pid}/kind", json={"kind": "set"})).status_code == 200
    view = (await async_client.get(f"{API}/products/{pid}/view")).json()
    # The first code is kept; both shapes go into the set.
    assert [(p["code"], p["quantity"], p["shape_id"]) for p in view["parts"]] == [(comps[0], 1, None)]
    recipe = sorted((r["shape_id"], r["quantity"]) for r in view["parts"][0]["recipe"])
    assert recipe == [(shapes[0], 1), (shapes[1], 1)]
    status = {c["code"]: c["retired"] for c in (await async_client.get(f"{API}/components")).json()}
    assert status[comps[1]] is False  # still used by "Solo DX"
    # Two of a piece per unit: its stock counts pieces, not sets, so the set gets a new code.
    cap = (await async_client.post(f"{API}/shapes", json={"name": "Tappo"})).json()["id"]
    caps = (await async_client.post(f"{API}/components", json={"shape_id": cap, "color": "Nero"})).json()["code"]
    r = await async_client.post(
        f"{API}/products", json={"name": "Due tappi", "parts": [{"component_code": caps, "quantity": 2}]}
    )
    two = r.json()["project_id"]
    await async_client.put(f"{API}/products/{two}/kind", json={"kind": "set"})
    view = (await async_client.get(f"{API}/products/{two}/view")).json()
    assert view["parts"][0]["code"] != caps
    assert [(r["shape_id"], r["quantity"]) for r in view["parts"][0]["recipe"]] == [(cap, 2)]
    # Kit parts from the product page: add, change, take out (retired when nobody else uses it).
    kit = (await async_client.post(f"{API}/products", json={"name": "Kit"})).json()["project_id"]
    loose = (await async_client.post(f"{API}/components", json={"name": "Vite"})).json()["code"]
    assert (await async_client.put(f"{API}/products/{kit}/parts/{loose}", json={"quantity": 4})).status_code == 200
    assert (await async_client.put(f"{API}/products/{kit}/parts/{comps[1]}", json={"quantity": 1})).status_code == 200
    view = (await async_client.get(f"{API}/products/{kit}/view")).json()
    assert [(p["code"], p["quantity"]) for p in view["parts"]] == [(loose, 4), (comps[1], 1)]
    await async_client.put(f"{API}/products/{kit}/parts/{loose}", json={"quantity": 0})
    await async_client.put(f"{API}/products/{kit}/parts/{comps[1]}", json={"quantity": 0})
    status = {c["code"]: c["retired"] for c in (await async_client.get(f"{API}/components")).json()}
    assert (status[loose], status[comps[1]]) == (True, False)  # the DX is still in "Solo DX"
    r = await async_client.put(f"{API}/products/{kit}/parts/{loose}", json={"quantity": 1})
    assert r.status_code == 409  # retired

    # A component Seller Hub knows can't be given a single shape back by mistake.
    r = await async_client.patch(f"{API}/components/{comps[0]}", json={"shape_id": shapes[0]})
    assert r.status_code == 422
