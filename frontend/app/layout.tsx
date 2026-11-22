import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ClipClip.ai — Studio Klip Podcast",
  description: "Temukan bagian terbaik podcast, rapikan subtitle Poppins, dan unduh klip MP4 berkualitas tinggi di komputer Anda.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="id"><body>{children}</body></html>;
}
