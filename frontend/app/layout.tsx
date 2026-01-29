import type { Metadata } from "next";
import "./globals.css";
import Navbar from "@/components/Navbar";
import SystemBar from "@/components/SystemBar";
import { ClientAuthProvider } from "@/lib/client-auth-provider";

export const metadata: Metadata = {
  title: "AION Analytics",
  description: "Predict. Learn. Evolve.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <ClientAuthProvider>
          <Navbar />
          <main className="mx-auto max-w-7xl px-4 pb-16 pt-6">{children}</main>
          <SystemBar />
        </ClientAuthProvider>
      </body>
    </html>
  );
}
