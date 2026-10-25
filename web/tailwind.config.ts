import type { Config } from 'tailwindcss'

// 디자인 토큰은 이 파일 한 곳에서 관리한다 (docs/화면_설계.md §3).
// 테마는 복숭아·코랄 계열(#FAB1A0). 등급 색은 빨강 경보 느낌을 피하려고 테마와 따로 차분한 파랑 단계색.
// 글자색은 바탕과 대비 4.5:1 이상 (WCAG AA). 청록·보라 글자는 *.ink, 지도 선·막대는 DEFAULT.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 흰 글자 대비: 500 4.9:1, 600 6.0:1, 700 7.6:1 (버튼·강조는 600 이상)
        brand: { 50: '#FFF3EF', 100: '#FDE2D9', 400: '#F08A6E', 500: '#BF4E34', 600: '#A8432C', 700: '#8A3624' },
        canvas: '#F2F4F8',
        surface: '#FFFFFF',
        ink: { 900: '#14171F', 800: '#1F2430', 600: '#4A5163', 400: '#8A91A3', 200: '#C9CEDA' },
        line: '#E3E7EF',
        lv0: '#DCE3EE',
        lv1: '#B9D3F0',
        lv2: '#5E9BD9',
        lv3: '#1F5FA8',
        hold: '#9AA1AE',
        scheduled: { DEFAULT: '#008F8A', soft: '#DDF2F1', ink: '#006B67' },
        deviation: { DEFAULT: '#7A5BC7', soft: '#ECE6F8', ink: '#5E3FA8' },
        caution: { DEFAULT: '#8A5A12', soft: '#FBF0DC' },
        okay: { DEFAULT: '#25704A', soft: '#E1F2E8' },
      },
      borderRadius: { card: '20px', pill: '999px' },
      boxShadow: { card: '0 4px 20px rgba(20,23,31,0.06)', lift: '0 10px 30px rgba(20,23,31,0.10)' },
      fontFamily: {
        sans: ['Pretendard', '"Apple SD Gothic Neo"', '"Malgun Gothic"', 'system-ui', 'sans-serif'],
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
        hero: 'linear-gradient(135deg, #FAB1A0 0%, #FBC6B8 55%, #FDDDD3 100%)', // 연한 바탕이라 글자는 진하게 (index.css .bg-hero, 대비 9:1 이상)
        holdstripe: 'repeating-linear-gradient(135deg, #E6E8EC 0 6px, #F4F5F7 6px 12px)',
      },
      spacing: { gutter: '24px' },
    },
  },
  plugins: [],
} satisfies Config
