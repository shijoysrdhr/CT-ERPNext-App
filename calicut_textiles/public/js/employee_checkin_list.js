frappe.listview_settings['Employee Checkin'] = {
    onload(listview) {
        listview.page.add_inner_button('Sync from CrossChex', () => {
            const today = frappe.datetime.get_today();
            const dialog = new frappe.ui.Dialog({
                title: 'Sync Punches from CrossChex',
                fields: [
                    {
                        label: 'From Date',
                        fieldname: 'from_date',
                        fieldtype: 'Date',
                        reqd: true,
                        default: frappe.datetime.add_months(today, -1)
                    },
                    {
                        label: 'To Date',
                        fieldname: 'to_date',
                        fieldtype: 'Date',
                        reqd: true,
                        default: today
                    },
                    {
                        fieldtype: 'HTML',
                        options: `<p class="text-muted small">Re-reads every punch in this period from
                            CrossChex Cloud. Punches the HR manager corrected upstream are updated here,
                            and punches deleted upstream are removed. Safe to run as often as you like —
                            it never creates duplicates.</p>
                            <p class="text-muted small">Runs in the background; a month takes a few
                            minutes. You will be notified when it finishes.</p>`
                    }
                ],
                primary_action_label: 'Sync',
                primary_action(values) {
                    if (values.from_date > values.to_date) {
                        frappe.msgprint(__('From Date must not be after To Date.'));
                        return;
                    }
                    dialog.hide();
                    frappe.call({
                        method: 'calicut_textiles.calicut_textiles.events.crosschex.enqueue_sync',
                        args: {
                            from_date: values.from_date,
                            to_date: values.to_date
                        },
                        freeze: true,
                        freeze_message: "Queueing check-in sync..."
                    });
                }
            });
            dialog.show();
        });

        listview.page.add_inner_button('Recalculation', () => {
            const dialog = new frappe.ui.Dialog({
                title: 'Recalculate Late/Early',
                fields: [
                    {
                        label: 'From Date',
                        fieldname: 'from_date',
                        fieldtype: 'Date',
                        reqd: true
                    },
                    {
                        label: 'To Date',
                        fieldname: 'to_date',
                        fieldtype: 'Date',
                        reqd: true
                    }
                ],
                primary_action_label: 'Recalculate',
                primary_action(values) {
                    dialog.hide();
                    frappe.call({
                        method: 'calicut_textiles.calicut_textiles.doctype.calicut_textiles_settings.calicut_textiles_settings.reset_late_early',
                        args: {
                            from_date: values.from_date,
                            to_date: values.to_date
                        },
                        freeze: true,
                        freeze_message: "Recalculating late/early entries...",
                        callback(r) {
                            if (r.message) {
                                frappe.msgprint(__('Late/Early entries recalculated successfully.'));
                                listview.refresh();
                            }
                        }
                    });
                }
            });
            dialog.show();
        });
    }
};
