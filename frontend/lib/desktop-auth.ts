/** Server-only desktop authentication. Never import this module from client UI. */
import { timingSafeEqual } from "node:crypto";

export function isPackagedDesktop() {
  return Boolean(process.env.PODCUT_PACKAGED_ROOT);
}

export function validDesktopRequest(request: Request) {
  if (!isPackagedDesktop()) return true;
  const expected = process.env.PODCUT_UI_TOKEN || "";
  const supplied = request.headers.get("x-podcut-ui-token") || "";
  if (!/^[a-f0-9]{64}$/i.test(expected) || !/^[a-f0-9]{64}$/i.test(supplied)) return false;
  return timingSafeEqual(Buffer.from(expected, "ascii"), Buffer.from(supplied, "ascii"));
}
