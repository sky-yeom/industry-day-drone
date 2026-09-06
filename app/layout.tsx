import type { Metadata } from "next";
import { Geist_Mono } from "next/font/google";
import "./globals.css";

// 등폭 폰트는 도구 이름 배지(VoiceControl)에서 font-mono 로 쓴다.
// 본문용 Geist(sans)는 subsets:["latin"] 이라 한글 글리프가 없고, body에서
// 시스템 폰트로 덮어쓰고 있어서 내려받기만 하고 쓰이지 않았다.
const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "드론 조사 대시보드",
  description: "Industry Day 실시간 드론 비행 계획 및 이상 징후 탐지 대시보드.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${geistMono.variable} h-full antialiased`}>
      <body className="h-full flex flex-col">{children}</body>
    </html>
  );
}
