// Types and helpers shared by the plugin UI components

export const API_ROOT = '/plugin/supplier-part-import';

export type SupplierInfo = {
  key: string;
  name: string;
  remaining_requests: number;
  company: { pk: number; name: string } | null;
};

export type SearchResult = {
  supplier: string;
  supplier_name: string;
  sku: string;
  mpn: string;
  manufacturer: string;
  description: string;
  packaging: string;
  image_url: string;
  product_url: string;
  status: string;
  discontinued: boolean;
  stock: number | null;
  unit_price: string;
  currency: string;
  existing: { pk: number; name: string; IPN: string; url: string } | null;
};

export type ParameterRow = {
  template: string;
  value: string;
  units: string;
  source: string;
  mapped: boolean;
  template_exists: boolean;
  include: boolean;
};

export type SupplierPartRow = {
  supplier: string;
  supplier_name: string;
  company: { pk: number; name: string } | null;
  sku: string;
  packaging: string;
  product_url: string;
  status: string;
  discontinued: boolean;
  stock: number | null;
  unit_price: string;
  currency: string;
  primary: boolean;
  include: boolean;
};

export type ExistingPart = {
  pk: number;
  name: string;
  IPN: string;
  url: string;
  match: 'MPN' | 'SKU' | 'name' | 'old name' | 'searched code';
  active: boolean;
  category: number | null;
  category_path: string;
  parameters: Record<string, string>;
  supplier_parts: { company: number; supplier: string; sku: string }[];
};

// Same as rules.PLACEHOLDER_VALUES: '-' and similar mean 'no value'
const PLACEHOLDER_VALUES = ['', '-', '--', '---', '—', '?', 'n/a', 'na', 'none', 'tbd'];

export function isPlaceholderValue(value: string | undefined): boolean {
  return PLACEHOLDER_VALUES.includes((value ?? '').trim().toLowerCase());
}

export function normCode(code: string): string {
  return (code || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

export type Preview = {
  token: string;
  part: {
    name: string;
    description: string;
    IPN: string;
    revision: string;
    category: number | null;
    category_path: string;
    // Category to create on import (pending mapping: it does not exist yet)
    new_category: string;
    image_url: string;
    datasheet_url: string;
  };
  manufacturer: string;
  mpn: string;
  parameter_source: string;
  supplier_category: string[];
  category_proposed: boolean;
  parameters: ParameterRow[];
  supplier_parts: SupplierPartRow[];
  declarations: { title: string; url: string }[];
  existing_part: ExistingPart | null;
  existing_parts: ExistingPart[];
  warnings: string[];
};

export type Proposal = {
  category: number | null;
  category_path: string;
  new_category: string;
  IPN: string;
  parameters: ParameterRow[];
};

export function errorMessage(error: any): string {
  return error?.response?.data?.error ?? error?.response?.data?.detail ?? error?.message ?? String(error);
}

export function formatPrice(value: string, currency: string): string {
  const number = Number(value);

  if (!value || Number.isNaN(number)) {
    return '-';
  }

  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'EUR',
      maximumFractionDigits: number < 1 ? 5 : 3,
    }).format(number);
  } catch {
    return `${value} ${currency}`;
  }
}

// Supplier requests can be slow (several suppliers, minimum delay between requests)
export const LONG_TIMEOUT = 120000;
