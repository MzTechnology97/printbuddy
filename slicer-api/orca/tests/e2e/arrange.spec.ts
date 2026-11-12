// Printbuddy: the /arrange route, with a stand-in for the slicer's CLI that records its arguments and "exports" the
// project it was given (the real CLI runs in the image's own check, slicer-api/orca/README.md).
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { promises as fs } from "fs";
import * as os from "os";
import * as path from "path";
import { request } from "./setup";
import { arrangeArgs } from "../../src/routes/arrange/route";

let dir: string;
let previous: string | undefined;

beforeAll(async () => {
  dir = await fs.mkdtemp(path.join(os.tmpdir(), "arrange-cli-"));
  const cli = path.join(dir, "fake-orca.sh");
  // the output dir follows --outputdir; the last argument is the project
  await fs.writeFile(
    cli,
    `#!/bin/sh
if [ "$1" = "--help" ]; then echo "--arrange --slice --allow-rotations --align-to-y-axis --export-3mf"; exit 0; fi
printf '%s\\n' "$@" > "${dir}/args.txt"
out=""; prev=""
for a in "$@"; do [ "$prev" = "--outputdir" ] && out="$a"; prev="$a"; last="$a"; done
case "$(head -c 20 "$last")" in *fail*) echo '{"return_code":-1,"error_string":"no room"}' > "$out/result.json"; exit 1;; esac
cp "$last" "$out/arranged.3mf"
`,
    { mode: 0o755 },
  );
  previous = process.env.ORCASLICER_PATH;
  process.env.ORCASLICER_PATH = cli;
});

afterAll(async () => {
  process.env.ORCASLICER_PATH = previous;
  await fs.rm(dir, { recursive: true, force: true });
});

const project = Buffer.from("PK fake 3mf");
const attach = { filename: "p.3mf", contentType: "model/3mf" };

describe("POST /arrange", () => {
  it("arranges every plate by default and answers the CLI's export", async () => {
    const res = await request
      .post("/arrange")
      .attach("file", project, attach)
      .buffer(true)
      .parse((r, done) => {
        const chunks: Buffer[] = [];
        r.on("data", (c: Buffer) => chunks.push(c));
        r.on("end", () => done(null, Buffer.concat(chunks)));
      });
    expect(res.status).toBe(200);
    expect(res.headers["content-type"]).toContain("model/3mf");
    expect(Buffer.from(res.body).toString()).toBe("PK fake 3mf");
    const args = (await fs.readFile(path.join(dir, "args.txt"), "utf-8")).trim().split("\n");
    expect(args.slice(0, 2)).toEqual(["--arrange", "1"]);
    expect(args).not.toContain("--slice"); // every plate: nothing to slice
    expect(args).toContain("--allow-multicolor-oneplate=1");
    expect(args).toContain("--export-3mf");
  });

  it("one plate is arranged by slicing it; the options go as --key=0|1", async () => {
    const res = await request
      .post("/arrange")
      .field("plate", "2")
      .field("allowRotations", "true")
      .field("alignToYAxis", "true")
      .field("allowMultiMaterial", "false")
      .attach("file", project, attach);
    expect(res.status).toBe(200);
    const args = (await fs.readFile(path.join(dir, "args.txt"), "utf-8")).trim().split("\n");
    expect(args.slice(0, 4)).toEqual(["--arrange", "1", "--slice", "2"]);
    // rotation on: Orca's dialog turns "Align to Y axis" off
    expect(args).toEqual(expect.arrayContaining(["--allow-rotations=1", "--align-to-y-axis=0", "--allow-multicolor-oneplate=0"]));
  });

  it("refuses what isn't a 3MF project or a plate, and says why the CLI failed", async () => {
    expect((await request.post("/arrange").field("plate", "1")).status).toBe(400);
    expect((await request.post("/arrange").field("plate", "37").attach("file", project, attach)).status).toBe(400);
    const failed = await request.post("/arrange").attach("file", Buffer.from("fail"), attach);
    expect(failed.status).toBe(422);
    expect(failed.body.message).toBe("Failed to arrange: no room");
  });

  it("builds Orca's arguments", () => {
    expect(arrangeArgs({ alignToYAxis: "1" }, "in.3mf", "out")).toEqual([
      "--arrange",
      "1",
      "--allow-rotations=0",
      "--allow-multicolor-oneplate=1",
      "--avoid-extrusion-cali-region=0",
      "--align-to-y-axis=1",
      "--allow-newer-file",
      "--export-3mf",
      "arranged.3mf",
      "--outputdir",
      "out",
      "in.3mf",
    ]);
    expect(() => arrangeArgs({ allowRotations: "maybe" }, "in", "out")).toThrow("Invalid boolean");
    // a CLI from before OrcaSlicer 2.5.0: no "Align to Y axis", refused when asked for
    const older = new Set(["arrange", "allow-rotations"]);
    expect(arrangeArgs({}, "in", "out", older)).not.toContain("--align-to-y-axis=0");
    expect(() => arrangeArgs({ alignToYAxis: "true" }, "in", "out", older)).toThrow("can't align to the Y axis");
  });
});
