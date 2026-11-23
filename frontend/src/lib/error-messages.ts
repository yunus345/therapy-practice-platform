export const AUTH_ERROR_MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "E-posta adresi veya şifre hatalı.",
  CSRF_INVALID: "Güvenlik kontrolü geçersiz. Sayfayı yenileyip tekrar deneyin.",
  NOT_AUTHENTICATED: "Bu işlem için giriş yapmalısınız.",
  MFA_REQUIRED: "Lütfen önce iki adımlı doğrulamayı tamamlayın.",
  TWO_FACTOR_SETUP_REQUIRED:
    "Personel hesabı için önce iki adımlı doğrulama kurulmalıdır.",
  RATE_LIMITED: "Çok fazla deneme yaptınız. Lütfen daha sonra tekrar deneyin.",
  TOKEN_INVALID: "Bağlantı geçersiz veya süresi dolmuş.",
  EMAIL_VERIFICATION_INVALID:
    "Doğrulama bağlantısı geçersiz veya süresi dolmuş.",
  PASSWORD_RESET_INVALID:
    "Şifre sıfırlama bağlantısı geçersiz veya süresi dolmuş.",
  TWO_FACTOR_INVALID: "İki adımlı doğrulama kodu geçersiz.",
  SLOT_TAKEN: "Seçilen saat başka biri tarafından alınmış.",
  SLOT_UNAVAILABLE: "Bu saat randevu için uygun değil.",
  DATE_RANGE_INVALID: "Tarih aralığı geçersiz.",
  CANCELLATION_TOO_LATE: "İptal süresi sona erdi.",
  RESCHEDULE_NOT_ALLOWED: "Bu randevu yeniden planlanamaz.",
  APPOINTMENT_NOT_FOUND: "Randevu bulunamadı.",
};

export function authErrorMessage(code?: string) {
  if (!code) return "İstek tamamlanamadı.";
  return AUTH_ERROR_MESSAGES[code] ?? "İstek tamamlanamadı.";
}
