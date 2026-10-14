import type { Config } from 'tailwindcss'

// 디자인 토큰은 이 파일 한 곳에서 관리한다 (docs/화면_설계.md §3).
// 등급 색은 빨강 경보 느낌을 피한 차분한 파랑 단계색.
// 글자색은 바탕과 대비 4.5:1 이상 (WCAG AA). 청록·보라 글자는 *.ink, 지도 선·막대는 DEFAULT.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: { 50: '#EEF3FE', 100: '#DCE6FC', 400: '#6C93F0', 500: '#3E6FE6', 600: '#2F5BD3', 700: '#2449AE' },
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
        hero: 'linear-gradient(135deg, #2449AE 0%, #2F5BD3 55%, #3A64D8 100%)', // 오른쪽 끝도 흰 글자 대비 5.2:1 (FE-11),
        holdstripe: 'repeating-linear-gradient(135deg, #E6E8EC 0 6px, #F4F5F7 6px 12px)',
      },
      spacing: { gutter: '24px' },
    },
  },
  plugins: [],
} satisfies Config
