import { useTheme } from '../contexts/ThemeContext';

/**
 * LexiSense logo B lockup (icon + wordmark) for the sign-in and sign-up cards.
 * The auth cards follow the app theme, so the light lockup (ink wordmark) is
 * used on the light card and the dark lockup (light wordmark) on the dark
 * card; the brand guide forbids the light version on dark backgrounds.
 */
export function BrandLockup({ className = 'h-10 w-auto' }) {
  const { theme } = useTheme();
  const variant = theme === 'dark' ? 'dark' : 'light';
  return (
    <img
      src={`${process.env.PUBLIC_URL || ''}/brand/lexisense-lockup-${variant}.svg`}
      alt="LexiSense"
      width="165"
      height="40"
      className={className}
    />
  );
}
