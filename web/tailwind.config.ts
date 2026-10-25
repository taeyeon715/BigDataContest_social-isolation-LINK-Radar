import type { Config } from 'tailwindcss'

// 디자인 토큰은 이 파일 한 곳에서 관리한다 (docs/화면_설계.md §3).
// 테마는 모래·베이지 한 계열(#EBE2CC · #DED6C1 · #CFC6B1 · #B7B09E)과 같은 색조를 진하게 내린 회갈색만 쓴다.
// 등급 색도 같은 계열의 단계(연할수록 Level 0, 진할수록 Level 3). 빨강 경보 느낌 없음.
// 글자색은 바탕과 대비 4.5:1 이상 (WCAG AA). 청록·보라 글자는 *.ink, 지도 선·막대는 DEFAULT.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 흰 글자 대비: 600 7.2:1, 700 9.6:1 (버튼·강조는 600 이상). 50~400 은 바탕·선
        brand: { 50: '#F6F1E6', 100: '#EBE2CC', 400: '#B7B09E', 500: '#8A8270', 600: '#5E5747', 700: '#4A4438' },
        canvas: '#F4EFE3',
        surface: '#FFFDF8',
        ink: { 900: '#2B2720', 800: '#3A352C', 600: '#5C5649', 400: '#8E8777', 200: '#CFC6B1' },
        line: '#DED6C1',
        lv0: '#EBE2CC',
        lv1: '#CFC6B1',
        lv2: '#9C9482', // 진한 글자 대비 5:1
        lv3: '#5E5747', // 흰 글자 대비 7.2:1
        hold: '#B7B09E',
        scheduled: { DEFAULT: '#7A7262', soft: '#EBE2CC', ink: '#4A4438' },
        deviation: { DEFAULT: '#8E8777', soft: '#DED6C1', ink: '#4A4438' },
        caution: { DEFAULT: '#6B5A3A', soft: '#EBE2CC' },
        okay: { DEFAULT: '#5E5747', soft: '#DED6C1' },
      },
      borderRadius: { card: '20px', pill: '999px' },
      boxShadow: { card: '0 4px 20px rgba(43,39,32,0.06)', lift: '0 10px 30px rgba(43,39,32,0.12)' },
      fontFamily: {
        sans: ['"Pretendard Variable"', 'Pretendard', '"Apple SD Gothic Neo"', '"Malgun Gothic"', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        xs: ['12px', '18px'],
        sm: ['14px', '21px'],
        base: ['16px', '24px'],
        lg: ['20px', '28px'],
        xl: ['28px', '36px'],
        'hero-num': ['44px', '52px'],
      },
      backgroundImage: {
        hero: 'linear-gradient(135deg, #DED6C1 0%, #EBE2CC 60%, #F2EBDA 100%)', // 연한 바탕이라 글자는 진하게 (index.css .bg-hero)
        holdstripe: 'repeating-linear-gradient(135deg, #DED6C1 0 6px, #F4EFE3 6px 12px)',
      },
      spacing: { gutter: '24px' },
    },
  },
  plugins: [],
} satisfies Config
