// Calicut Textiles — Sales Invoice Counter (CT/RT) logic.
//
// Consolidated from these DB Client Scripts (now DISABLED in favour of this file,
// loaded via doctype_js):
//   - Sales Invoice Counter Defaults   (branch defaults, POS, naming series,
//                                        GST template resolution, guards, SO->SI copy, is_return)
//   - Counter RT Inclusive Tax         (merged into set_tax_template_auto + a taxes_and_charges re-swap)
//   - Counter Mandatory in SI          (merged into validate)
//   - Counter from SO to SI            (already duplicated inside Counter Defaults; kept here once)
//   - Paid amount check in SI          (overpayment alert)
//   - Sales invoice rate check after batch change
//   - (Add Freight button removed 2026-07-23; the Freight field replaced it)
//
// NOTE: server-side correctness for GST-inclusive Counter RT billing is guaranteed by the
// before_validate hook `enforce_counter_rt_inclusive_tax`. The inclusive swap below is only
// for the cashier's live on-screen total; do not rely on client logic for the math.

(function () {

  // ===== Config =====
  const COUNTER_CFG = {
    'Counter CT': { pos_profile: 'Counter CT', normal_token: 'CT26.', return_token: 'CTRET26.' },
    'Counter RT': { pos_profile: 'Counter RT', normal_token: 'RT26.', return_token: 'RTRET26.' }
  };

  // User -> default branch mapping
  const USER_DEFAULT_BRANCH = {
    'sivasankaranck2@gmail.com': 'Counter RT',
    'safeenactex@gmail.com':     'Counter RT',
    'sangeethamctex@gmail.com':  'Counter RT'
  };

  // Users allowed to bypass series + counter-mandatory enforcement (e.g. TEST. series)
  const SERIES_BYPASS_USERS = ['shijoysrdhr@gmail.com'];

  const PREFERRED_TEMPLATE_NAMES = {
    IN_STATE:  'Output GST In-state',
    OUT_STATE: 'Output GST Out-state'
  };

  // Counter RT inclusive-template map (exclusive -> GST-inclusive twin)
  const RT_INCLUSIVE_MAP = {
    'Output GST In-state - CT':  'Output GST In-state - Inc - CT',
    'Output GST Out-state - CT': 'Output GST Out-state - Inc - CT'
  };
  const RT_INCLUSIVE_NAMES = new Set(Object.values(RT_INCLUSIVE_MAP));

  const GST_STATE_NAME_BY_CODE = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh',
    '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
    '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
    '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal',
    '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
    '25': 'Dadra and Nagar Haveli and Daman and Diu', '26': 'Dadra and Nagar Haveli and Daman and Diu',
    '27': 'Maharashtra', '28': 'Andhra Pradesh (Before Division)', '29': 'Karnataka', '30': 'Goa',
    '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry',
    '35': 'Andaman and Nicobar Islands', '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
    '96': 'Other Country', '97': 'Other Territory', '99': 'Centre Jurisdiction'
  };

  // ===== Utils =====
  const norm = s => String(s || '').trim().toLowerCase();

  function company_state_code_from_gstin(gstin) {
    const code = String(gstin || '').slice(0, 2);
    return /^\d{2}$/.test(code) ? code : '';
  }

  function extract_pos_code_and_name(pos) {
    const s = String(pos || '').trim();
    const m = s.match(/^(\d{2})\s*[-–]?\s*(.*)$/);
    if (m) {
      const code = m[1];
      const name = (m[2] || GST_STATE_NAME_BY_CODE[code] || '').trim();
      return { code, name };
    }
    return { code: '', name: s };
  }

  function get_required_token(cfg, is_return) {
    return is_return ? cfg.return_token : cfg.normal_token;
  }

  function is_series_bypass_user() {
    return SERIES_BYPASS_USERS.includes(frappe.session.user);
  }

  // ===== Core wiring =====
  frappe.ui.form.on('Sales Invoice', {

    onload: async function (frm) {
      // Apply user default branch on new invoice
      if (frm.doc.__islocal && !frm.doc.custom_branch) {
        const default_branch = USER_DEFAULT_BRANCH[frappe.session.user];
        if (default_branch) {
          await frm.set_value('custom_branch', default_branch);
        }
      }

      // Apply POS / series / tax template for Counter invoices
      if (frm.doc.custom_branch?.startsWith('Counter') && frm.doc.docstatus === 0) {
        frappe.after_ajax(async () => {
          await apply_branch_defaults(frm);
          await set_tax_template_auto(frm);
        });
      }
    },

    custom_branch: async function (frm) {
      await apply_branch_defaults(frm);
      await set_tax_template_auto(frm);
    },

    is_return: async function (frm) {
      // Uncheck Include Payment (POS) for returns
      if (Number(frm.doc.is_return) === 1 && Number(frm.doc.is_pos) === 1) {
        await frm.set_value('is_pos', 0);
        frappe.show_alert({
          message: __('Include Payment (POS) has been unchecked for return invoice'),
          indicator: 'orange'
        });
      }
      await apply_branch_defaults(frm);
      await set_tax_template_auto(frm);
    },

    customer:        async frm => { await set_tax_template_auto(frm); },
    company:         async frm => { await set_tax_template_auto(frm); },
    place_of_supply: async frm => { await set_tax_template_auto(frm); },
    company_gstin:   async frm => { await set_tax_template_auto(frm); },

    // Re-assert the Counter RT inclusive twin whenever the template changes
    // (e.g. India Compliance rebuilds it back to the exclusive template).
    taxes_and_charges: frm => { apply_counter_rt_inclusive(frm); },

    validate(frm) {
      // 0. Counter mandatory (bypass for admin)
      if (!is_series_bypass_user() && !frm.doc.custom_branch) {
        frappe.msgprint({
          title: __('Counter Required'),
          indicator: 'red',
          message: __('You must select a Counter before saving a Sales Invoice.')
        });
        frappe.validated = false;
        return;
      }

      // 1. Zero rate guard (item level)
      const zero_rate_items = [];
      (frm.doc.items || []).forEach((item, idx) => {
        if (!item.rate || Number(item.rate) === 0) {
          zero_rate_items.push(`${idx + 1}. ${item.item_code || item.item_name || '(no item)'}`);
        }
      });
      if (zero_rate_items.length) {
        frappe.msgprint({
          title: __('Invalid Rate'),
          indicator: 'red',
          message: __('Following items have rate as 0:<br><br>') + zero_rate_items.join('<br>')
        });
        frappe.validated = false;
        return;
      }

      // 2. Zero tax guard (only when no GST template is selected)
      if (!frm.doc.taxes_and_charges && frm.doc.total_taxes_and_charges === 0) {
        frappe.msgprint({
          title: __('Check Tax'),
          indicator: 'red',
          message: __('Total Taxes and Charges is 0. Re-enter Customer')
        });
        frappe.validated = false;
        return;
      }

      // 3. Series enforcement (bypass for admin users)
      if (is_series_bypass_user()) return;

      const cfg = COUNTER_CFG[frm.doc.custom_branch];
      if (cfg) {
        const isReturn      = !!Number(frm.doc.is_return);
        const requiredToken = get_required_token(cfg, isReturn);
        const current       = frm.doc.naming_series || '';
        if (!current.startsWith(requiredToken)) {
          frappe.msgprint({
            title: __('Wrong Naming Series'),
            indicator: 'red',
            message: __(
              `For <b>${frm.doc.custom_branch}</b> ${isReturn ? 'Return' : 'Invoice'}, `
              + `naming series must start with <b>${requiredToken}</b>. `
              + `Currently selected: <b>${current}</b>.`
            )
          });
          frappe.validated = false;
          return;
        }
      }
    }
  });

  // ===== Sales Order -> Sales Invoice: copy counter =====
  frappe.ui.form.on('Sales Invoice', {
    refresh(frm) {
      if (!frm.doc.__islocal || frm.doc.custom_branch) return;
      const so_name = frm.doc.items && frm.doc.items[0] && frm.doc.items[0].sales_order;
      if (!so_name) return;
      frappe.db.get_value('Sales Order', so_name, 'custom_counter').then(r => {
        const counter = r.message && r.message.custom_counter;
        if (counter) frm.set_value('custom_branch', counter);
      });
    }
  });

  // ===== Branch defaults (POS + naming series) =====
  async function apply_branch_defaults(frm) {
    if (!frm?.doc?.custom_branch) return;
    const cfg = COUNTER_CFG[frm.doc.custom_branch];
    if (!cfg) return;

    if (frm.doc.pos_profile !== cfg.pos_profile) {
      await frm.set_value('pos_profile', cfg.pos_profile);
    }

    const isReturn = !!Number(frm.doc.is_return);
    const token    = get_required_token(cfg, isReturn);

    const options = (frm.fields_dict.naming_series?.df?.options || '')
      .split('\n').map(s => (s || '').trim()).filter(Boolean);
    const desiredSeries = options.find(opt => opt.startsWith(token));

    if (!desiredSeries) {
      frappe.msgprint({
        title: __('Series Not Found'),
        message: __(`No naming series starting with <b>${token}</b> found. Please check Naming Series configuration.`),
        indicator: 'orange'
      });
      return;
    }
    if (frm.doc.naming_series !== desiredSeries) {
      await frm.set_value('naming_series', desiredSeries);
      frm.refresh_field('naming_series');
    }
  }

  // ===== Tax template =====
  async function set_tax_template_auto(frm) {
    if (!frm.doc.company) return;

    const compCode = company_state_code_from_gstin(frm.doc.company_gstin);
    const { code: posCode, name: posName } = extract_pos_code_and_name(frm.doc.place_of_supply);
    if (!compCode && !posName) return;

    let inState = false;
    if (compCode && posCode) {
      inState = compCode === posCode;
    } else if (posName) {
      const compName = GST_STATE_NAME_BY_CODE[compCode] || '';
      if (compName) inState = norm(compName) === norm(posName);
    }

    // Resolve straight to the FINAL template the invoice should carry. Counter
    // branches bill GST-inclusive, so ask for the '- Inc' twin by name instead of
    // resolving the exclusive one and swapping afterwards. The old order set the
    // exclusive template first -- which dirties the form via set_value -- and only
    // then swapped to the inclusive twin, so every reopened Counter invoice came
    // up "Not Saved" even though its stored template was already correct.
    const isCounter = !!COUNTER_CFG[frm.doc.custom_branch];
    const preferred = (inState ? PREFERRED_TEMPLATE_NAMES.IN_STATE : PREFERRED_TEMPLATE_NAMES.OUT_STATE)
                      + (isCounter ? ' - Inc' : '');
    let resolved = await find_template_via_search_link(frm.doc.company, preferred);
    if (!resolved) {
      resolved = await find_best_template_by_keywords(frm.doc.company, inState ? 'in' : 'out');
    }

    if (resolved && frm.doc.taxes_and_charges !== resolved) {
      await frm.set_value('taxes_and_charges', resolved);
      await frm.trigger('taxes_and_charges');
    }

    // Safety net if the exclusive template still slipped through (e.g. keyword
    // fallback): swap it to the GST-inclusive twin. A no-op once resolved is '- Inc'.
    await apply_counter_rt_inclusive(frm);
  }

  // ===== Counter RT inclusive swap + live-form enforcement =====
  // Merged from "Counter RT Inclusive Tax", plus a client-side mirror of the
  // before_validate server hook (enforce_counter_rt_inclusive_tax) so the cashier
  // sees the GST-INCLUSIVE grand total BEFORE save — important because POS payment
  // is collected on that figure.
  let _rt_incl_timer = null;

  async function apply_counter_rt_inclusive(frm) {
    if (frm.doc.pos_profile !== 'Counter RT') return;
    const target = RT_INCLUSIVE_MAP[frm.doc.taxes_and_charges];
    if (target && frm.doc.taxes_and_charges !== target) {
      await frm.set_value('taxes_and_charges', target);
    }
    // India Compliance rebuilds the GST rows as exclusive asynchronously; flip them
    // back to inclusive once it settles so the live total is correct.
    schedule_rt_inclusive(frm);
  }

  function schedule_rt_inclusive(frm) {
    if (frm.doc.pos_profile !== 'Counter RT' || frm.doc.docstatus !== 0) return;
    clearTimeout(_rt_incl_timer);
    _rt_incl_timer = setTimeout(() => enforce_rt_inclusive_rows(frm), 800);
  }

  async function enforce_rt_inclusive_rows(frm) {
    if (frm.doc.pos_profile !== 'Counter RT' || frm.doc.docstatus !== 0) return;
    const tmpl = frm.doc.taxes_and_charges;
    if (!tmpl || !RT_INCLUSIVE_NAMES.has(tmpl)) return;

    // Already the clean inclusive structure? The ONLY reliable signal is
    // included_in_print_rate on the GST rows -- that is what "inclusive" means, and
    // it is exactly what distinguishes the "- Inc" templates from their exclusive
    // twins. Nothing else is safe to test:
    //
    //   * charge_type differs BETWEEN the two inclusive templates --
    //       In-state - Inc  -> SGST/CGST as "On Net Total"
    //       Out-state - Inc -> IGST      as "On Previous Row Total"
    //     so requiring "On Net Total" made this permanently false for every
    //     out-of-state customer.
    //   * an "Actual" row is not evidence of an exclusive rebuild either: both
    //     inclusive templates ship a Freight Outward "Actual" row of their own.
    //
    // Each wrong test produced the same failure -- the function runs on refresh,
    // which fires after every save, so it re-fetched the taxes table, dirtied the
    // form and blanked the per-row tax amounts, then did it again on the next
    // refresh. The invoice returned to "Not Saved" the instant it was saved.
    // Non-GST rows are left alone here, exactly as the before_validate server hook
    // leaves them.
    const taxes = frm.doc.taxes || [];
    const gstRows = taxes.filter(t => t.account_head && t.account_head.indexOf('GST') !== -1);
    const clean = gstRows.length && gstRows.every(t => cint(t.included_in_print_rate));
    if (clean) return;

    // India Compliance rebuilt the rows from the EXCLUSIVE template (Freight "Actual" +
    // "On Previous Row Total"). Replace them with the inclusive template's clean
    // "On Net Total" rows: gives the correct inclusive total live, and avoids ERPNext's
    // inclusive-tax validation error (an inclusive "On Previous Row Total" would require
    // the referenced Freight Actual row to be inclusive too, which Actual charges can't).
    try {
      const r = await frappe.call({
        method: 'erpnext.controllers.accounts_controller.get_taxes_and_charges',
        args: { master_doctype: 'Sales Taxes and Charges Template', master_name: tmpl }
      });
      if (r && r.message) {
        await frm.set_value('taxes', r.message);
      }
    } catch (e) {
      // leave as-is; the before_validate server hook still guarantees inclusive on save
    }
  }

  async function find_template_via_search_link(company, txt) {
    if (!txt) return null;
    try {
      const r = await frappe.call({
        method: 'frappe.desk.search.search_link',
        args: { doctype: 'Sales Taxes and Charges Template', txt, filters: { company, disabled: 0 }, page_length: 50 }
      });
      const rows = r?.message || [];
      // Match on label: x.value is the document NAME and always carries the
      // ' - <company abbr>' suffix, so === against the bare title never matched
      // and this silently fell through to rows[0] (the exclusive template).
      const exact = rows.find(x => x.label === txt);
      return exact ? exact.value : (rows[0]?.value || null);
    } catch { return null; }
  }

  async function find_best_template_by_keywords(company, want) {
    const txt = want === 'in' ? 'in cgst sgst' : 'out igst inter';
    try {
      const r = await frappe.call({
        method: 'frappe.desk.search.search_link',
        args: { doctype: 'Sales Taxes and Charges Template', txt, filters: { company, disabled: 0 }, page_length: 100 }
      });
      const rows = r?.message || [];
      if (!rows.length) return null;

      const IN_TOKENS  = ['in-state', 'in state', 'cgst', 'sgst', 'within', 'intra'];
      const OUT_TOKENS = ['out-state', 'out of state', 'igst', 'inter', 'interstate', 'outstate'];
      const tokens = want === 'in' ? IN_TOKENS : OUT_TOKENS;

      const withScores = rows.map(x => {
        const text = `${x.value} ${x.description || ''}`.toLowerCase();
        let score = 0;
        tokens.forEach(t => { if (text.includes(t)) score++; });
        if (text.includes('default')) score += 0.3;
        return { name: x.value, score };
      }).filter(x => x.score > 0);

      if (!withScores.length) return rows[0].value;
      withScores.sort((a, b) => b.score - a.score);
      return withScores[0].name;
    } catch { return null; }
  }

  // ===== Overpayment alert (from "Paid amount check in SI") =====
  frappe.ui.form.on('Sales Invoice Payment', {
    amount: function (frm) { check_overpayment(frm); }
  });
  frappe.ui.form.on('Sales Invoice', {
    payments_remove: function (frm) { check_overpayment(frm); }
  });
  function check_overpayment(frm) {
    if (!frm.doc.payments || frm.doc.payments.length === 0) return;
    let total_paid = 0;
    frm.doc.payments.forEach(row => { total_paid += flt(row.amount); });
    let grand_total = flt(frm.doc.rounded_total) || flt(frm.doc.grand_total);
    if (grand_total > 0 && total_paid > grand_total) {
      let excess = total_paid - grand_total;
      frappe.msgprint({
        title: __('Overpayment Alert'),
        message: __('Total collected <b>{0}</b> exceeds Grand Total <b>{1}</b> by <b>{2}</b>', [
          format_currency(total_paid, frm.doc.currency),
          format_currency(grand_total, frm.doc.currency),
          format_currency(excess, frm.doc.currency)
        ]),
        indicator: 'orange'
      });
    }
  }

  // ===== Batch rate-reset warning (from "Sales invoice rate check after batch change") =====
  frappe.ui.form.on('Sales Invoice Item', {
    batch_no(frm, cdt, cdn) {
      const row = locals[cdt][cdn];
      if (!row) return;
      const old_rate = flt(row.rate);
      const old_plr  = flt(row.price_list_rate);
      const item     = row.item_name || row.item_code || '';
      if (!(old_plr && Math.abs(old_rate - old_plr) > 0.005)) return;
      setTimeout(() => {
        const r = locals[cdt] && locals[cdt][cdn];
        if (!r) return;
        const new_rate = flt(r.rate);
        if (Math.abs(new_rate - old_rate) > 0.005) {
          frappe.msgprint({
            title: __('Rate reset by batch change'),
            indicator: 'orange',
            message: __('Item <b>{0}</b>: your manually entered rate <b>{1}</b> was replaced with <b>{2}</b> (the new batch’s price-list rate). Please re-check and re-apply the discount if needed.',
              [item, format_currency(old_rate, frm.doc.currency), format_currency(new_rate, frm.doc.currency)])
          });
        }
      }, 1200);
    }
  });

  // Re-assert inclusive rows on load / counter change (catches IC's late rebuilds).
  frappe.ui.form.on('Sales Invoice', {
    refresh: frm => schedule_rt_inclusive(frm),
    pos_profile: frm => schedule_rt_inclusive(frm)
  });

  // ===== Default the print format to "RT Compact" for Counter RT =====
  // The doctype-wide "Default Print Format" can't be conditional, so set it per-doc.
  // frm.meta is shared, so remember the original default and restore it for non-RT.
  frappe.ui.form.on('Sales Invoice', {
    refresh: set_rt_print_format,
    custom_branch: set_rt_print_format
  });
  function set_rt_print_format(frm) {
    const meta = frm.meta;
    if (!meta) return;
    if (meta._ct_orig_default_pf === undefined) {
      meta._ct_orig_default_pf = meta.default_print_format || '';
    }
    meta.default_print_format = (frm.doc.custom_branch === 'Counter RT')
      ? 'RT Compact'
      : meta._ct_orig_default_pf;
  }

  // The "Add Freight" button that inserted IAF64174 as a line item was removed
  // once the Freight field started filling the Freight Outward Charges tax row
  // instead. Two ways to enter freight would have produced two different
  // invoices from the same amount.

  // ===== Freight field -> Freight Outward Charges tax row =====
  // The cashier types what the customer pays for freight, GST included. An
  // "Actual" charge cannot itself be inclusive, so on a "- Inc" template the row
  // must hold the pre-tax value; the GST rows ("On Previous Row Total" pointing
  // at the freight row) add the tax back. This mirrors the authoritative server
  // hook (events.sales_invoice.apply_freight_charge) purely so the cashier sees
  // the total move before saving -- the server is what actually decides.
  frappe.ui.form.on('Sales Invoice', {
    custom_freight_amount: preview_freight,
    taxes_and_charges: preview_freight,
  });

  function invoice_gst_rate(frm) {
    const gst_accounts = (frm.doc.taxes || [])
      .filter(t => t.account_head && t.account_head.indexOf('GST') !== -1)
      .map(t => t.account_head);
    if (!gst_accounts.length) return null;

    const rates = new Set();
    (frm.doc.items || []).forEach(item => {
      if (!item.item_tax_rate) return;
      let map;
      try { map = JSON.parse(item.item_tax_rate); } catch (e) { return; }
      const rate = gst_accounts.reduce((sum, acc) => sum + flt(map[acc]), 0);
      if (rate) rates.add(Math.round(rate * 10000) / 10000);
    });
    return rates.size === 1 ? [...rates][0] : null;
  }

  function preview_freight(frm) {
    const row = (frm.doc.taxes || []).find(
      t => t.charge_type === 'Actual' && t.account_head && t.account_head.indexOf('Freight') !== -1
    );
    if (!row) return;

    const entered = flt(frm.doc.custom_freight_amount);
    if (!entered) {
      frappe.model.set_value(row.doctype, row.name, 'tax_amount', 0);
      return;
    }

    const inclusive = (frm.doc.taxes_and_charges || '').replace(/\s/g, '').indexOf('-Inc-') !== -1;
    if (!inclusive) {
      frappe.model.set_value(row.doctype, row.name, 'tax_amount', entered);
      return;
    }

    const rate = invoice_gst_rate(frm);
    if (rate === null) {
      // Leave it to the server to refuse, with its clearer message.
      return;
    }
    frappe.model.set_value(
      row.doctype, row.name, 'tax_amount', flt(entered / (1 + rate / 100), 2)
    );
  }

})();
