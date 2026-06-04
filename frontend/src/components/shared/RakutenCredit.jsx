/**
 * RakutenCredit — mandatory Rakuten Web Service credit badge.
 *
 * Rakuten's terms state: "Use the provided HTML source code as is.
 * Modified HTML is not permissible." The exact anchor/img markup is
 * reproduced here. dangerouslySetInnerHTML is intentional and safe —
 * this is a static, trusted constant with no user input.
 *
 * Must be displayed wherever Rakuten hotel data is visible.
 * Ref: https://webservice.rakuten.co.jp/guide/credit
 */

const RAKUTEN_BADGE_HTML =
  '<a href="https://webservice.rakuten.co.jp/" target="_blank">' +
  '<img src="https://webservice.rakuten.co.jp/img/credit/200709/credit_22121.gif" ' +
  'border="0" alt="Rakuten Web Service Center" title="Rakuten Web Service Center" ' +
  'width="221" height="21"/>' +
  '</a>';

export default function RakutenCredit({ className = '' }) {
  return (
    <div
      className={`flex items-center justify-center py-2 ${className}`}
      // eslint-disable-next-line react/no-danger
      dangerouslySetInnerHTML={{ __html: RAKUTEN_BADGE_HTML }}
    />
  );
}
