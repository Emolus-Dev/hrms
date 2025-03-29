# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from datetime import datetime

import frappe
from frappe import _, bold
from frappe.query_builder.functions import Sum
from frappe.utils import cstr, flt, get_datetime, get_link_to_form

from erpnext.accounts.general_ledger import make_gl_entries
from erpnext.controllers.accounts_controller import AccountsController


class Gratuity(AccountsController):
	def validate(self):
		data = self.calculate_work_experience_and_amount()
		self.current_work_experience = data["current_work_experience"]
		self.custom_total_working_days = data["total_working_days"]
		self.custom_start_date = data["start_date"]
		self.custom_end_date = data["end_date"]
		self.amount = data["amount"]
		self.custom_slips_detail = str(data["slips_detail"])
		self.set_status()

	@property
	def gratuity_settings(self):
		if not hasattr(self, "_gratuity_settings"):
			self._gratuity_settings = frappe.db.get_value(
				"Gratuity Rule",
				self.gratuity_rule,
				[
					"work_experience_calculation_function as method",
					"total_working_days_per_year",
					"minimum_year_for_gratuity",
					"calculate_gratuity_amount_based_on",
					#"last_slabs",
					"based_on"
				],
				as_dict=True,
			)

		return self._gratuity_settings

	def set_status(self, update=False):
		status = {"0": "Draft", "1": "Submitted", "2": "Cancelled"}[cstr(self.docstatus or 0)]

		if self.docstatus == 1:
			precision = self.precision("paid_amount")
			if flt(self.paid_amount) > 0 and flt(self.amount, precision) == flt(self.paid_amount, precision):
				status = "Paid"
			else:
				status = "Unpaid"

		if update and self.status != status:
			self.db_set("status", status)
		else:
			self.status = status
	

	def on_submit(self):
		if self.custom_pay_via == "Salary Slip":
			self.create_additional_salary()
		elif self.custom_pay_via == "Journal Entry":
			self.create_gl_entries()
		elif self.custom_pay_via == "Full and Final Statement":
			self.create_full_and_final_statement()

	def on_cancel(self):
		self.ignore_linked_doctypes = ["GL Entry"]
		self.create_gl_entries(cancel=True)
		self.set_status(update=True)

	def create_gl_entries(self, cancel=False):
		gl_entries = self.get_gl_entries()
		make_gl_entries(gl_entries, cancel)

	def get_gl_entries(self):
		gl_entry = []
		# payable entry
		# if self.amount:
		if self.amount and self.custom_pay_via == "Journal Entry":
			gl_entry.append(
				self.get_gl_dict(
					{
						"account": self.payable_account,
						"credit": self.amount,
						"credit_in_account_currency": self.amount,
						"against": self.expense_account,
						"party_type": "Employee",
						"party": self.employee,
						"against_voucher_type": self.doctype,
						"against_voucher": self.name,
						"cost_center": self.cost_center,
					},
					item=self,
				)
			)

			# expense entries
			gl_entry.append(
				self.get_gl_dict(
					{
						"account": self.expense_account,
						"debit": self.amount,
						"debit_in_account_currency": self.amount,
						"against": self.payable_account,
						"cost_center": self.cost_center,
					},
					item=self,
				)
			)
		else:
			frappe.msgprint(_("Total Amount cannot be zero"))

		return gl_entry

	def create_additional_salary(self):
		#if self.custom_pay_via == "Salary Slip" :
		additional_salary = frappe.new_doc("Additional Salary")
		additional_salary.employee = self.employee
		additional_salary.salary_component = self.salary_component
		additional_salary.overwrite_salary_structure_amount = 0
		additional_salary.amount = self.amount
		additional_salary.payroll_date = self.payroll_date
		additional_salary.company = self.company
		additional_salary.ref_doctype = self.doctype
		additional_salary.ref_docname = self.name
		additional_salary.submit()

	def create_full_and_final_statement(self):
		full_and_final_statement_exists = frappe.get_all(
			"Full and Final Statement",
			filters=[
				["employee", "=", self.employee],
				["company", "=", self.company],
				["docstatus", "in", [0,1]],
			],
			fields=["name", "docstatus"]
		)

		gratuity_component = frappe.db.get_value("Gratuity Rule", self.gratuity_rule, "custom_salary_component")
		gratuity_component_account = frappe.db.get_value("Salary Component Account", {"parent": gratuity_component, "company": self.company}, "account")
		if not full_and_final_statement_exists:
			full_and_final_statement = frappe.new_doc("Full and Final Statement")
			full_and_final_statement.employee = self.employee
			full_and_final_statement.transaction_date = self.posting_date
			full_and_final_statement.company = self.company
			full_and_final_statement.docstatus = 0
			full_and_final_statement.relieving_date = frappe.db.get_value(
				"Employee", self.employee, ["relieving_date"]
			)
			full_and_final_statement.append("payables", {
				"component": gratuity_component,
				"reference_document_type": "Gratuity",
				"reference_document": self.name,
				"amount": self.amount,
				"account": gratuity_component_account
			})
			full_and_final_statement.insert(ignore_mandatory=True, ignore_links=True)
		else:
			for fafs in full_and_final_statement_exists:
				if fafs.docstatus == 0:
					full_and_final_statement = frappe.get_doc("Full and Final Statement", fafs.name)

					existing_components = {row.component for row in full_and_final_statement.payables}

					if self.gratuity_rule not in existing_components:
						# Agregar si no existe
						full_and_final_statement.append("payables", {
							"component": self.gratuity_rule,
							"reference_document_type": "Gratuity",
							"reference_document": self.name,
							"amount": self.amount,
							"account": gratuity_component_account
						})
					else:
						# Opcional: Eliminar duplicados antes de guardar
						unique_payables = []
						seen_components = set()

						for row in full_and_final_statement.payables:
							if row.component not in seen_components:
								unique_payables.append(row)
								seen_components.add(row.component)

						full_and_final_statement.payables = unique_payables  # Asigna solo elementos únicos

					# Guardar cambios
					full_and_final_statement.save()
				else:
					frappe.throw(f"A Full and Final Statement already exists for employee: {self.employee}")

	def set_total_advance_paid(self):
		gle = frappe.qb.DocType("GL Entry")
		paid_amount = (
			frappe.qb.from_(gle)
			.select(Sum(gle.debit_in_account_currency).as_("paid_amount"))
			.where(
				(gle.against_voucher_type == "Gratuity")
				& (gle.against_voucher == self.name)
				& (gle.party_type == "Employee")
				& (gle.party == self.employee)
				& (gle.docstatus == 1)
				& (gle.is_cancelled == 0)
			)
		).run(as_dict=True)[0].paid_amount or 0

		if flt(paid_amount) > self.amount:
			frappe.throw(_("Row {0}# Paid Amount cannot be greater than Total amount"))

		self.db_set("paid_amount", paid_amount)
		self.set_status(update=True)

	@frappe.whitelist()
	def calculate_work_experience_and_amount(self) -> dict:
		if self.gratuity_settings.method == "Manual":
			current_work_experience = flt(self.current_work_experience)
		else:
			# current_work_experience = self.get_work_experience()
			current_work_experience, total_working_days, start_date, end_date = self.get_work_experience()

		gratuity_amount, slips_detail = self.get_gratuity_amount(current_work_experience)
		
		# return {"current_work_experience": current_work_experience, "amount": gratuity_amount}
		return {
			"current_work_experience": current_work_experience,
			"amount": gratuity_amount,
			"total_working_days": total_working_days,
			"start_date": start_date,
			"end_date": end_date,
			"slips_detail": slips_detail,
		}

	def get_work_experience(self) -> float:
		total_working_days, start_date, end_date = self.get_total_working_days()
		rule = self.gratuity_settings
		work_experience = total_working_days / (rule.total_working_days_per_year or 1)

		if rule.method == "Round off Work Experience":
			work_experience = round(work_experience)
		elif rule.method == "Exact Time":
			work_experience = flt(work_experience)
		else:
			work_experience = flt(work_experience, self.precision("current_work_experience"))

		if work_experience < rule.minimum_year_for_gratuity:
			frappe.throw(
				_("Employee: {0} has to complete a minimum of {1} years for gratuity").format(
					bold(self.employee), rule.minimum_year_for_gratuity
				)
			)
		return work_experience or 0, total_working_days or 0, start_date or 0, end_date or 0

	def get_total_working_days(self) -> float:
		date_of_joining, relieving_date, previous_employee, employee_full_name = frappe.db.get_value(
				"Employee", self.employee, ["date_of_joining", "relieving_date", "custom_previous_employee", "employee_name"]
			)

		if date_of_joining is None:
			frappe.throw(f"El empleado { employee_full_name } no tiene fecha de ingreso registrada.")

		# since_the_most_recent
		self.gratuity_rule_doc = frappe.get_doc("Gratuity Rule", self.gratuity_rule)

		if self.gratuity_rule_doc.based_on == "Components":
			month_number = int(self.gratuity_rule_doc.since_the_most_recent.split(".")[0])
			year = relieving_date.year if relieving_date.month >= month_number else relieving_date.year - 1
			most_recent_month = datetime(year, month_number, 1)

			start_since_the_most_recent = most_recent_month.strftime("%Y-%m-%d")

		if self.custom_extraordinary_payroll:
			if not self.custom_start_date:
				self.custom_start_date = start_since_the_most_recent
			if not self.custom_end_date:
				one_year = datetime(year+1, month_number-1, 1)
				self.custom_end_date = one_year.strftime("%Y-%m-%d")

			date_of_joining = self.custom_start_date
			relieving_date = self.custom_end_date

		if self.gratuity_rule_doc.since_the_most_recent:
			if start_since_the_most_recent < date_of_joining.strftime("%Y-%m-%d"):
				date_of_joining = start_since_the_most_recent

		employees = [self.employee]
		if previous_employee:
			date_of_joining = frappe.db.get_value(
			"Employee", self.employee, ["date_of_joining"]
		)
			employees.append(previous_employee)

		if not relieving_date and self.custom_extraordinary_payroll == 0:
			frappe.throw(
				_("Please set Relieving Date for employee: {0}").format(
					bold(get_link_to_form("Employee", self.employee))
				)
			)

		total_working_days = (get_datetime(relieving_date) - get_datetime(date_of_joining)).days
		total_days = total_working_days

		payroll_based_on = frappe.db.get_single_value("Payroll Settings", "payroll_based_on") or "Leave"

		if payroll_based_on == "Leave":
			total_lwp = self.get_non_working_days(relieving_date, "On Leave")
			total_working_days -= total_lwp
		elif payroll_based_on == "Attendance":
			total_absent = self.get_non_working_days(relieving_date, "Absent")
			total_working_days -= total_absent

		if self.custom_extraordinary_payroll or self.gratuity_rule_doc.since_the_most_recent or self.gratuity_rule_doc.custom_last_slips:
			total_working_days_query = """
				SELECT SUM(total_working_days) AS dias_entre_fechas
				FROM `tabSalary Slip`
				WHERE docstatus = 1 AND employee IN %s
				AND posting_date BETWEEN %s AND %s;
			"""

			twd = frappe.db.sql(total_working_days_query, (tuple(employees), date_of_joining, relieving_date))

			total_working_days = twd[0][0] if twd and twd[0][0] else 0

		if self.gratuity_rule_doc.based_on == "Pending Leaves":
			allocations = frappe.db.get_all(
				"Leave Allocation",
				filters={"employee": self.employee, "leave_type": self.gratuity_rule_doc.leave_type, "docstatus": 1},
				fields=["new_leaves_allocated"],
			)

			if not allocations:
				return 0  # Si no hay asignaciones para este tipo de licencia, retornar 0

			total_allocated = sum(flt(allocation["new_leaves_allocated"]) for allocation in allocations)

			# Obtener las aplicaciones de licencia aprobadas para este tipo de licencia
			approved_leaves = frappe.db.get_all(
				"Leave Application",
				filters={"employee": self.employee, "leave_type": self.gratuity_rule_doc.leave_type, "docstatus": 1, "status": "Approved"},
				fields=["total_leave_days"],
			)

			# Calcular los días usados para este tipo de licencia
			used_leaves = sum(flt(leave["total_leave_days"]) for leave in approved_leaves)

			# Calcular los días pendientes (asignados - usados)
			total_working_days = flt(total_allocated) - used_leaves

		if total_working_days > total_days:
			frappe.throw(f"Verifique la cantidad de días trabajados del empleado { employee_full_name } en la información de Apertura.")

		return total_working_days, date_of_joining, relieving_date

	def get_non_working_days(self, relieving_date: str, status: str) -> float:
		filters = {
			"docstatus": 1,
			"status": status,
			"employee": self.employee,
			"attendance_date": ("<=", get_datetime(relieving_date)),
		}

		if status == "On Leave":
			lwp_leave_types = frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name")
			filters["leave_type"] = ("IN", lwp_leave_types)

		record = frappe.get_all("Attendance", filters=filters, fields=["COUNT(*) as total_lwp"])
		return record[0].total_lwp if len(record) else 0

	def get_last_salary_structure_assignment(self) -> dict:
		self.salary_structure = frappe.db.get_value(
			"Salary Structure Assignment",
			{"employee": self.employee, "docstatus": 1},
			["salary_structure"],
			order_by="from_date DESC",
			as_dict=True
		)
		self.salary_structure_values = frappe.db.get_value(
			"Salary Structure",
			{"name": self.salary_structure.salary_structure},
			["payroll_frequency"],
			as_dict=True
		)

	def get_gratuity_amount(self, experience: float) -> float:
		total_component_amount, slips_detail = self.get_total_component_amount()

		calculate_amount_based_on = self.gratuity_settings.calculate_gratuity_amount_based_on
		based_on = self.gratuity_settings.based_on

		gratuity_amount = 0
		slabs = self.get_gratuity_rule_slabs()
		slab_found = False
		years_left = experience

		for slab in slabs:
			if calculate_amount_based_on == "Current Slab":
				if self._is_experience_within_slab(slab, experience):
					gratuity_amount = (
						total_component_amount * experience * slab.fraction_of_applicable_earnings
					)
					self.get_last_salary_structure_assignment()

					if based_on == "Pending Leaves" and flt(self.custom_total_working_days) > 0:
						if self.salary_structure_values.payroll_frequency == "Monthly":
							working_days = 30
						elif self.salary_structure_values.payroll_frequency == "Weekly":
							working_days = 7
						elif self.salary_structure_values.payroll_frequency == "Fortnightly":
							working_days = 14
						elif self.salary_structure_values.payroll_frequency == "Bimonthly":
							working_days = 15
						elif self.salary_structure_values.payroll_frequency == "Daily":
							working_days = 1

						gratuity_amount = (
							(total_component_amount / working_days * slab.fraction_of_applicable_earnings) * self.custom_total_working_days
						)
					if slab.fraction_of_applicable_earnings:
						slab_found = True

				if slab_found:
					break

			elif calculate_amount_based_on == "Sum of all previous slabs":
				# no slabs, fraction applicable for all years
				if slab.to_year == 0 and slab.from_year == 0:
					gratuity_amount += (
						years_left * total_component_amount * slab.fraction_of_applicable_earnings
					)
					slab_found = True
					break

				if self._is_experience_beyond_slab(slab, experience):
					gratuity_amount += (
						(slab.to_year - slab.from_year)
						* total_component_amount
						* slab.fraction_of_applicable_earnings
					)
					years_left -= slab.to_year - slab.from_year
					slab_found = True

				elif self._is_experience_within_slab(slab, experience):
					gratuity_amount += (
						years_left * total_component_amount * slab.fraction_of_applicable_earnings
					)
					slab_found = True
					break
    
		if not slab_found:
			frappe.throw(
				_(
					"No applicable slab found for the calculation of gratuity amount as per the Gratuity Rule: {0}"
				).format(bold(self.gratuity_rule))
			)

		return flt(gratuity_amount, self.precision("amount")), slips_detail

	def get_total_component_amount(self) -> float:
		from collections import defaultdict
		#if self.based_on == "Components":
		applicable_earning_components = self.get_applicable_components()
		salary_slips = get_last_salary_slips(self.employee, self.gratuity_rule)
		if not salary_slips:
			frappe.throw(_("No Salary Slip found for Employee: {0}").format(bold(self.employee)))

		slips_detail = {}
		total_amount = 0
		monthly_salaries = defaultdict(list)
		for salary_slip in salary_slips:
			# consider full payment days for calculation as last month's salary slip
			# might have less payment days as per attendance, making it non-deterministic
			salary_slip.payment_days = salary_slip.total_working_days
			#salary_slip.calculate_net_pay()
			component_found = False

			earnings = frappe.get_all("Salary Detail", {"parent": salary_slip.name, "parentfield": "earnings", "parenttype":"Salary Slip"}, ["salary_component", "amount"])

			for row in earnings:
				if row.salary_component in applicable_earning_components:
					total_amount += flt(row.amount)
					component_found = True
					slips_detail.setdefault(salary_slip.name, {}).setdefault(salary_slip.end_date, {}).setdefault(row.salary_component, flt(row.amount))
					month_year = salary_slip["start_date"].strftime("%Y-%m")
					monthly_salaries[month_year].append(row["amount"])

			if not component_found:
				frappe.throw(
					_("No applicable Earning component found in last salary slip for Gratuity Rule: {0}").format(
						bold(get_link_to_form("Gratuity Rule", self.gratuity_rule))
					)
			)
			

		average_monthly_salary = 0
		total_months = len(monthly_salaries)
	
		for salaries in monthly_salaries.values():
			average_monthly_salary += sum(salaries) / len(salaries)

		if total_months > 0:
			average_monthly_salary = average_monthly_salary / total_months
		else:
			average_monthly_salary = 0
   

		return average_monthly_salary, slips_detail

	def get_applicable_components(self) -> list[str]:
		applicable_earning_components = frappe.get_all(
			"Gratuity Applicable Component", filters={"parent": self.gratuity_rule}, pluck="salary_component"
		)
		if not applicable_earning_components and self.gratuity_rule_doc.based_on=="Components":
			frappe.throw(
				_("No applicable Earning components found for Gratuity Rule: {0}").format(
					bold(get_link_to_form("Gratuity Rule", self.gratuity_rule))
				)
			)

		return applicable_earning_components

	def get_gratuity_rule_slabs(self) -> list[dict]:
		return frappe.get_all(
			"Gratuity Rule Slab",
			filters={"parent": self.gratuity_rule},
			fields=["from_year", "to_year", "fraction_of_applicable_earnings"],
			order_by="idx",
		)

	def _is_experience_within_slab(self, slab: dict, experience: float) -> bool:
		return bool(slab.from_year <= experience and (experience <= slab.to_year or slab.to_year == 0))

	def _is_experience_beyond_slab(self, slab: dict, experience: float) -> bool:
		return bool(slab.from_year < experience and (slab.to_year < experience and slab.to_year != 0))


def get_last_salary_slips(employee: str, gratuity: str) -> dict | None:
	from dateutil.relativedelta import relativedelta

	last_slips = frappe.get_value("Gratuity Rule", gratuity, "custom_last_slips")
	if not last_slips > 0:
		last_slips = 1

	end_date = frappe.db.get_value(
		"Salary Slip",
		{"employee": employee, "docstatus": 1},
		"end_date",  # Solo un campo, devuelve un valor directo
		order_by="end_date DESC"
	)

	if not end_date:
		frappe.throw(_("No Salary Slip found for Employee: {0}").format(bold(employee)))

	end_date = str(end_date)
	end_date = datetime.strptime(end_date, "%Y-%m-%d")
	fecha_limite = end_date - relativedelta(months=last_slips)
	fecha_limite = fecha_limite.replace(day=1)
	fecha_limite_str = fecha_limite.strftime('%Y-%m-%d')

	salary_slips = frappe.db.get_all(
		"Salary Slip",
		filters={
			"employee": employee,
			"docstatus": 1,
			"start_date": [">=", fecha_limite_str],
			"end_date": ["<=", end_date] 
		},
		fields=["name","start_date","end_date","total_working_days","gross_pay"],
		order_by="start_date DESC",
	)
	if not salary_slips:
		salary_slips = []

	return salary_slips