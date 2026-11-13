"""Printfarm (fork): OrcaSlicer's Arrange for the workspace, through the slicer sidecar's /arrange (slicer-api/orca).

The project goes out with every instance on a plate given an identify_id; the CLI's export comes back with its own
object numbering, and each instance is found again by its identify_id (else, on no plate, by a unique name), its
transform corrected when the CLI moved the mesh about its origin. A stand-in sidecar answers here (MockTransport);
the real one is checked by its own tests and image.
"""

from __future__ import annotations

import io
import re
import zipfile

import httpx
import pytest

from backend.app.farm.workspace import arrange, project, routes
from backend.tests.integration.test_farm_workspace import API, add_file, make_project, open_ws, project_settings

AREA = ["0x0", "256x0", "256x256", "0x256"]


def rezip(data: bytes, edit) -> bytes:
    """``data`` with each entry's bytes passed through ``edit(name, bytes)``."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for info in src.infolist():
            out.writestr(info.filename, edit(info.filename, src.read(info)))
    return buf.getvalue()


def as_orca_exports(sent: bytes, moves: dict[str, str], plates: dict[str, int]) -> bytes:
    """What the CLI's export looks like: object 2 renumbered 9, the transforms in ``moves`` (object id -> the
    transform), the instances listed on ``plates`` (object id -> plate) with their identify_ids."""

    def edit(name: str, b: bytes) -> bytes:
        if name == project.MODEL_ENTRY:
            for oid, t in moves.items():
                b = re.sub(rb'(<item objectid="' + oid.encode() + rb'" transform=")[^"]*', rb"\g<1>" + t.encode(), b)
            return b.replace(b'id="2"', b'id="9"').replace(b'objectid="2"', b'objectid="9"')
        if name == project.MODEL_SETTINGS_ENTRY:
            s = b.decode()
            found = re.findall(r'(<model_instance><metadata key="object_id" value="(\d+)"/>.*?</model_instance>)', s)
            entries = {oid: entry for entry, oid in found}
            body = re.sub(r"<plate>.*?</plate>\n?", "", s, flags=re.S)
            for n in range(1, max(plates.values()) + 1):
                listed = "".join(entries[o] for o, p in plates.items() if p == n)
                plate = f'<plate><metadata key="plater_id" value="{n}"/>{listed}</plate>\n'
                body = body.replace("</config>", plate + "</config>")
            body = body.replace('<object id="2">', '<object id="9">')
            return body.replace('"object_id" value="2"', '"object_id" value="9"').encode()
        return b

    return rezip(sent, edit)


class TestPrepare:
    def test_every_instance_on_a_plate_gets_an_identify_id(self):
        sent, ids = arrange.prepare(make_project("orca"))
        settings = zipfile.ZipFile(io.BytesIO(sent)).read(project.MODEL_SETTINGS_ENTRY)
        assert ids == {"1": ("2", 0), "2": ("4", 0)}
        assert (
            b'<metadata key="instance_id" value="0"/><metadata key="identify_id" value="1"/></model_instance>'
            in settings
        )
        # a file that has them all goes as it is
        assert arrange.prepare(sent) == (sent, ids)


class TestReadResult:
    def test_instances_are_found_by_identify_id_with_their_plates(self):
        sent, ids = arrange.prepare(make_project("orca"))
        moves = {"2": "0 -1 0 1 0 0 0 0 1 120 80 5", "4": "1 0 0 0 1 0 0 0 1 400 90 5"}
        placed, plates = arrange.read_result(sent, ids, as_orca_exports(sent, moves, {"2": 2, "4": 2}))
        assert plates == 2
        assert placed == [
            {"object_id": "2", "instance": 0, "matrix": [0, -1, 0, 1, 0, 0, 0, 0, 1, 120, 80, 5], "plate": 2},
            {"object_id": "4", "instance": 0, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 400, 90, 5], "plate": 2},
        ]

    def test_a_mesh_moved_about_its_origin_is_made_up_for(self):
        sent, ids = arrange.prepare(make_project("orca"))
        out = as_orca_exports(sent, {"2": "0 -1 0 1 0 0 0 0 1 120 80 5"}, {"2": 1, "4": 2})

        def shift(name: str, b: bytes) -> bytes:
            if name != "3D/Objects/object_1.model":
                return b
            return re.sub(
                rb'<vertex x="([-\d.e]+)" y="([-\d.e]+)"',
                lambda m: f'<vertex x="{float(m[1]) + 1:g}" y="{float(m[2]) + 2:g}"'.encode(),
                b,
            )

        # the CLI's mesh of object 2 moved by (1, 2, 0) in its own coordinates: its transform takes that back,
        # through the turn (x -> -y, y -> x): +(2, -1)
        placed, _ = arrange.read_result(sent, ids, rezip(out, shift))
        assert placed[0]["matrix"][9:12] == [122, 79, 5]

    def test_an_unknown_answer_is_refused(self):
        sent, ids = arrange.prepare(make_project("orca"))
        with pytest.raises(arrange.InvalidArrange, match="not a 3MF project"):
            arrange.read_result(sent, ids, b"not a zip")


@pytest.fixture
def sidecar(monkeypatch):
    """A stand-in OrcaSlicer sidecar: ``state["answer"](request)`` -> httpx.Response; the requests it got."""
    seen: list[httpx.Request] = []
    state: dict = {}
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return state["answer"](request)

    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def url(_db, _slicer):
        return "http://sidecar.test"

    monkeypatch.setattr(routes, "sidecar_url", url)
    return state, seen


def sent_file(request: httpx.Request) -> bytes:
    """The 3MF the backend posted (the multipart's file part)."""
    body = request.content
    return body[body.index(b"PK\x03\x04") : body.rindex(b"\r\n--")]


async def new_file(async_client, db_session) -> tuple[int, dict]:
    f = await add_file(db_session, make_project("orca", project_settings(printable_area=AREA)))
    return f.id, await open_ws(async_client, f.id)


@pytest.mark.integration
class TestArrangeRoute:
    async def test_orcas_arrangement_comes_back_as_placements(self, async_client, db_session, sidecar):
        state, seen = sidecar
        state["answer"] = lambda req: httpx.Response(
            200, content=as_orca_exports(sent_file(req), {"2": "1 0 0 0 1 0 0 0 1 120 80 5"}, {"2": 1, "4": 1})
        )
        file_id, doc = await new_file(async_client, db_session)
        body = {"base_sha256": doc["sha256"], "arrange": {"plate": 0, "allow_rotations": True}}
        resp = await async_client.post(f"{API}/{file_id}/arrange", json=body)
        assert resp.status_code == 200, resp.text
        placements = resp.json()["placements"]
        assert placements[0] == {
            "object_id": "2",
            "instance": 0,
            "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 120, 80, 5],
            "plate": 1,
        }
        assert [p["plate"] for p in placements] == [1, 1]
        assert seen[0].url == "http://sidecar.test/arrange"
        assert b'name="plate"\r\n\r\n0' in seen[0].content
        assert b'name="allowRotations"\r\n\r\ntrue' in seen[0].content

    async def test_the_draft_goes_with_it_but_unsaved_plates_dont(self, async_client, db_session, sidecar):
        state, seen = sidecar
        state["answer"] = lambda req: httpx.Response(200, content=as_orca_exports(sent_file(req), {}, {"2": 1, "4": 2}))
        file_id, doc = await new_file(async_client, db_session)
        moved = [{"object_id": "2", "instance": 0, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 77, 50, 5]}]
        resp = await async_client.post(
            f"{API}/{file_id}/arrange", json={"base_sha256": doc["sha256"], "transforms": moved}
        )
        assert resp.status_code == 200, resp.text
        model = zipfile.ZipFile(io.BytesIO(sent_file(seen[0]))).read(project.MODEL_ENTRY)
        assert b'transform="1 0 0 0 1 0 0 0 1 77 50 5"' in model
        resp = await async_client.post(
            f"{API}/{file_id}/arrange", json={"base_sha256": doc["sha256"], "plate_structure": [{"op": "add"}]}
        )
        assert resp.status_code == 422 and "Save the copy" in resp.json()["detail"]

    async def test_more_plates_than_the_project_has_are_refused(self, async_client, db_session, sidecar):
        state, _ = sidecar
        state["answer"] = lambda req: httpx.Response(200, content=as_orca_exports(sent_file(req), {}, {"2": 1, "4": 3}))
        file_id, doc = await new_file(async_client, db_session)
        resp = await async_client.post(f"{API}/{file_id}/arrange", json={"base_sha256": doc["sha256"]})
        assert resp.status_code == 422 and "need 3 plates: add 1 more" in resp.json()["detail"]

    @pytest.mark.parametrize(
        ("answer", "status", "detail"),
        [
            (httpx.Response(404, json={"message": "Not Found"}), 502, "needs Printbuddy's image"),
            (httpx.Response(422, json={"message": "Failed to arrange: no room"}), 422, "no room"),
            (httpx.Response(500, json={"message": "boom"}), 502, "boom"),
        ],
    )
    async def test_the_sidecars_failures_say_why(self, async_client, db_session, sidecar, answer, status, detail):
        state, _ = sidecar
        state["answer"] = lambda req: answer
        file_id, doc = await new_file(async_client, db_session)
        resp = await async_client.post(f"{API}/{file_id}/arrange", json={"base_sha256": doc["sha256"]})
        assert resp.status_code == status and detail in resp.json()["detail"]
