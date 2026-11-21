import { NextResponse, type NextRequest } from "next/server";
import { isPackagedDesktop, validDesktopRequest } from "./lib/desktop-auth";

/** Pages/assets are guarded here; streaming /api routes enforce the same token
 * themselves. Running POST uploads through Next proxy would clone/truncate the
 * body at 10 MB, defeating video uploads and the offline model package. */
export function proxy(request: NextRequest) {
  if (isPackagedDesktop() && (request.headers.get("host") !== "127.0.0.1:3300" ||
      (request.headers.has("origin") && request.headers.get("origin") !== "http://127.0.0.1:3300") ||
      !validDesktopRequest(request))) {
    return NextResponse.json({ detail: "Open the private PODCUT desktop application." }, { status: 403 });
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!api/).*)"] };
