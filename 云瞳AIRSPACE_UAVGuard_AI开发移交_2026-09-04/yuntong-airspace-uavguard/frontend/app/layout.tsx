import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import './globals.css';
import './palette.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export function generateMetadata(): Metadata {
  // Deployment-controlled canonical origin. Never derive it from Host/forwarded headers.
  const origin = new URL(process.env.SITE_ORIGIN || 'http://localhost:3000');
  const title = '云瞳 AIRSPACE · 校园低空安全监测平台';
  const description =
    '校园空域三维态势、多源视频与目标轨迹联动的监控工作台。当前为模拟数据演示。';
  const image = new URL('/og.png', origin).toString();
  return {
    metadataBase: origin,
    title,
    description,
    icons: { icon: '/favicon.svg' },
    openGraph: {
      title,
      description,
      type: 'website',
      locale: 'zh_CN',
      images: [{ url: image, width: 1731, height: 909, alt: title }],
    },
    twitter: {
      card: 'summary_large_image',
      title,
      description,
      images: [image],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <head>
        <meta name="darkreader-lock" />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
