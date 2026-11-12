// Printbuddy: OrcaSlicer's own Arrange, without slicing what doesn't need it. POST /arrange with a 3MF project (field
// "file") and Orca's arrange options; the answer is the project as Orca's CLI exports it after arranging
// (--arrange 1 --export-3mf), for Printbuddy to read the instances' new places from (by their identify_id).
//
// plate: 0 arranges every plate's objects together (Orca's "Arrange all objects", objects can change plate); N only
// plate N's (its "Arrange objects on current plate"). The CLI arranges a single plate only when it slices it
// (--slice N), so that call slices the plate too and throws the G-code away.
import { Router } from "express";
import { execFile } from "child_process";
import { promises as fs } from "fs";
import * as os from "os";
import * as path from "path";
import { uploadModel } from "../../middleware/upload";
import { AppError } from "../../middleware/error";

const router = Router();

const MAX_PLATES = 36;
const timeoutMs = Number(process.env.ARRANGE_TIMEOUT_MS) > 0 ? Number(process.env.ARRANGE_TIMEOUT_MS) : 10 * 60 * 1000;

/** A form field as a boolean ("true"/"1" on, "false"/"0" off), ``fallback`` when absent. */
function flag(value: unknown, fallback: boolean): boolean {
  if (value === undefined || value === "") return fallback;
  if (value === true || value === "true" || value === "1") return true;
  if (value === false || value === "false" || value === "0") return false;
  throw new AppError(400, `Invalid boolean: ${String(value)}`);
}

/** The CLI's arguments: Orca's ArrangeSettings (its arrange dialog) as the CLI takes them. Spacing has no CLI option:
 * the CLI arranges with 0, "auto spacing". Booleans are written --key=0|1 (the CLI reads no "--no-" form).
 * ``options``: the CLI's own (``--help``); "Align to Y axis" came with OrcaSlicer 2.5.0, so an older CLI goes without
 * it, and refuses it when it is asked for. */
export function arrangeArgs(
  body: Record<string, unknown>,
  input: string,
  outputDir: string,
  options: Set<string> = new Set(["align-to-y-axis"]),
): string[] {
  const plate = Number(body.plate ?? 0);
  if (!Number.isInteger(plate) || plate < 0 || plate > MAX_PLATES) {
    throw new AppError(400, `plate must be 0 (every plate) to ${MAX_PLATES}`);
  }
  const on = (key: string, fallback: boolean) => (flag(body[key], fallback) ? "1" : "0");
  const args = ["--arrange", "1"];
  if (plate > 0) args.push("--slice", String(plate));
  args.push(`--allow-rotations=${on("allowRotations", false)}`);
  args.push(`--allow-multicolor-oneplate=${on("allowMultiMaterial", true)}`);
  args.push(`--avoid-extrusion-cali-region=${on("avoidCalibrationRegion", false)}`);
  // Orca turns this off while rotation is on (_render_arrange_menu)
  const alignY = flag(body.allowRotations, false) ? "0" : on("alignToYAxis", false);
  if (options.has("align-to-y-axis")) args.push(`--align-to-y-axis=${alignY}`);
  else if (alignY === "1") throw new AppError(422, "This OrcaSlicer can't align to the Y axis (its CLI came with that in 2.5.0)");
  args.push("--allow-newer-file", "--export-3mf", "arranged.3mf", "--outputdir", outputDir, input);
  return args;
}

let cliOptions: Promise<Set<string>> | null = null;

/** The CLI's options, from its --help (once; a failed read is tried again next time). */
function optionsOf(cli: string): Promise<Set<string>> {
  cliOptions ??= new Promise<Set<string>>((resolve) => {
    execFile(cli, ["--help"], { encoding: "utf-8", timeout: 60_000, maxBuffer: 16 * 1024 * 1024 }, (_error, stdout, stderr) =>
      resolve(new Set(`${stdout}
${stderr}`.match(/--[a-z0-9][a-z0-9-]*/g)?.map((o) => o.slice(2)) ?? [])),
    );
  }).then((found) => {
    if (!found.has("arrange")) cliOptions = null;
    return found;
  });
  return cliOptions;
}

function run(cli: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(cli, args, { encoding: "utf-8", timeout: timeoutMs, maxBuffer: 64 * 1024 * 1024 }, (error) =>
      error ? reject(error) : resolve(),
    );
  });
}

router.post("/", uploadModel.single("file"), async (req, res, next) => {
  let workdir: string | null = null;
  try {
    const file = req.file;
    if (!file || path.extname(file.originalname).toLowerCase() !== ".3mf") {
      throw new AppError(400, "A 3MF project (field \"file\") is required");
    }
    const cli = process.env.ORCASLICER_PATH;
    if (!cli) {
      throw new AppError(500, "Arranging is not configured on the server", "ORCASLICER_PATH is not defined");
    }
    workdir = await fs.mkdtemp(path.join(os.tmpdir(), "arrange-"));
    const input = path.join(workdir, "input.3mf");
    const outputDir = path.join(workdir, "output");
    await fs.mkdir(outputDir);
    await fs.writeFile(input, file.buffer);
    const args = arrangeArgs(req.body ?? {}, input, outputDir, await optionsOf(cli));
    try {
      await run(cli, args);
    } catch (error) {
      // the CLI's own reason, when it wrote one (result.json, as the slice route reads it)
      let reason: string | undefined;
      try {
        reason = JSON.parse(await fs.readFile(path.join(outputDir, "result.json"), "utf-8"))?.error_string;
      } catch {
        reason = undefined;
      }
      throw new AppError(
        422,
        reason ? `Failed to arrange: ${reason}` : "Failed to arrange the project",
        error instanceof Error ? error.message : String(error),
      );
    }
    let arranged: Buffer;
    try {
      arranged = await fs.readFile(path.join(outputDir, "arranged.3mf"));
    } catch {
      throw new AppError(500, "The slicer did not export the arranged project");
    }
    res.setHeader("Content-Type", "model/3mf");
    res.setHeader("Content-Disposition", 'attachment; filename="arranged.3mf"');
    res.send(arranged);
  } catch (error) {
    next(error);
  } finally {
    if (workdir) await fs.rm(workdir, { recursive: true, force: true }).catch(() => undefined);
  }
});

export default router;
