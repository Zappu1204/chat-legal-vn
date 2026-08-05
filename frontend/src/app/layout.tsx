import type { Metadata } from "next";
import { Inter, Lora } from "next/font/google";
import { Providers } from "@/components/Providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin", "vietnamese"], variable: "--font-inter" });
const lora = Lora({ subsets: ["latin", "vietnamese"], variable: "--font-lora" });

export const metadata: Metadata = {
  title: "Chat Legal VN — Trợ lý pháp luật giao thông",
  description:
    "Hỏi đáp pháp luật Việt Nam với câu trả lời có trích dẫn điều khoản cụ thể.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi" className={`${inter.variable} ${lora.variable}`}>
      <body className="font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
