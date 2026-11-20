import { ApiEndpoints, apiUrl, checkPluginVersion, type InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  Image,
  Loader,
  Paper,
  Radio,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  API_ROOT,
  type ExistingPart,
  errorMessage,
  formatPrice,
  isPlaceholderValue,
  LONG_TIMEOUT,
  normCode,
  type ParameterRow,
  type Preview,
  type Proposal,
  type SearchResult,
  type SupplierInfo,
  type SupplierPartRow,
} from './api';

function useCategories(context: InvenTreePluginContext) {
  const [categories, setCategories] = useState<{ value: string; label: string }[]>([]);

  useEffect(() => {
    context.api
      .get(apiUrl(ApiEndpoints.category_list), { params: { limit: 5000 } })
      .then((response) => {
        const rows = response.data?.results ?? response.data ?? [];
        setCategories(
          rows
            .map((c: any) => ({ value: String(c.pk), label: c.pathstring ?? c.name }))
            .sort((a: any, b: any) => a.label.localeCompare(b.label)),
        );
      })
      .catch(() => setCategories([]));
  }, [context.api]);

  return categories;
}

function SearchResults({
  results,
  onSelect,
  loading,
}: {
  results: SearchResult[];
  onSelect: (r: SearchResult) => void;
  loading: string | null;
}) {
  return (
    <Table striped highlightOnHover>
      <Table.Thead>
        <Table.Tr>
          <Table.Th />
          <Table.Th>Supplier</Table.Th>
          <Table.Th>SKU</Table.Th>
          <Table.Th>MPN</Table.Th>
          <Table.Th>Manufacturer</Table.Th>
          <Table.Th>Description</Table.Th>
          <Table.Th>Packaging</Table.Th>
          <Table.Th>Stock</Table.Th>
          <Table.Th>Price @ 1</Table.Th>
          <Table.Th />
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {results.map((r) => (
          <Table.Tr key={`${r.supplier}-${r.sku}`}>
            <Table.Td>{r.image_url && <Image src={r.image_url} w={40} h={40} fit='contain' />}</Table.Td>
            <Table.Td>{r.supplier_name}</Table.Td>
            <Table.Td>
              <Anchor href={r.product_url} target='_blank' rel='noreferrer' size='sm'>
                {r.sku}
              </Anchor>
            </Table.Td>
            <Table.Td>{r.mpn}</Table.Td>
            <Table.Td>{r.manufacturer}</Table.Td>
            <Table.Td>
              <Text size='sm'>{r.description}</Text>
              {r.discontinued && (
                <Badge color='orange' variant='light'>
                  {r.status}
                </Badge>
              )}
            </Table.Td>
            <Table.Td>{r.packaging}</Table.Td>
            <Table.Td>{r.stock ?? '-'}</Table.Td>
            <Table.Td>{formatPrice(r.unit_price, r.currency)}</Table.Td>
            <Table.Td>
              <Group gap='xs' wrap='nowrap'>
                {r.existing && (
                  <Badge color='blue' variant='light' title={r.existing.name}>
                    {r.existing.IPN || 'In InvenTree'}
                  </Badge>
                )}
                <Button
                  size='xs'
                  onClick={() => onSelect(r)}
                  loading={loading === `${r.supplier}-${r.sku}`}
                  disabled={!!loading}
                >
                  Select
                </Button>
              </Group>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

const MATCH_LABELS: Record<string, string> = {
  'old name': 'its MPN is an old name of this product',
  'searched code': 'same code as the search: check that it is the same product',
};

function PreviewForm({
  context,
  preview,
  onDone,
}: {
  context: InvenTreePluginContext;
  preview: Preview;
  onDone: () => void;
}) {
  const categories = useCategories(context);
  const [form, setForm] = useState(preview);
  // Part to update ('new' = create a new part); an existing part is proposed first, to avoid duplicates.
  // A part matching only the searched code is not preselected: a keyword search may return other products
  const proposed = preview.existing_parts.find((p) => p.match !== 'searched code');
  const [target, setTarget] = useState<string>(proposed ? String(proposed.pk) : 'new');
  const [addMissingParameters, setAddMissingParameters] = useState<boolean>(true);
  const [rememberCategory, setRememberCategory] = useState<boolean>(!preview.category_proposed);
  const [downloadImage, setDownloadImage] = useState<boolean>(true);
  const [attachDatasheet, setAttachDatasheet] = useState<boolean>(true);
  const [attachDeclarations, setAttachDeclarations] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);

  const setPart = (field: string, value: any) => setForm((f) => ({ ...f, part: { ...f.part, [field]: value } }));

  const changeCategory = useCallback(
    (value: string | null) => {
      // An existing category replaces the one to create
      setForm((f) => ({ ...f, part: { ...f.part, category: value ? Number(value) : null, new_category: '' } }));

      if (!value) {
        return;
      }

      // Parameters mapping and IPN depend on the category
      context.api
        .get(`${API_ROOT}/proposal/`, { params: { token: preview.token, category: value } })
        .then((response) => {
          const proposal: Proposal = response.data;
          setForm((f) => ({
            ...f,
            part: { ...f.part, IPN: proposal.IPN || f.part.IPN, category_path: proposal.category_path },
            parameters: proposal.parameters,
          }));
        })
        .catch((err) => notifications.show({ title: 'Import', message: errorMessage(err), color: 'red' }));
    },
    [context.api, preview.token],
  );

  const setParameter = (index: number, field: string, value: any) =>
    setForm((f) => ({
      ...f,
      parameters: f.parameters.map((p, i) => (i === index ? { ...p, [field]: value } : p)),
    }));

  const targetPart: ExistingPart | undefined = preview.existing_parts.find((p) => String(p.pk) === target);
  const useExisting = !!targetPart;

  // Supplier parts already on the chosen part: same supplier and SKU, or only the same supplier
  const presence = useCallback(
    (row: SupplierPartRow): 'same' | 'supplier' | null => {
      const existing = targetPart?.supplier_parts ?? [];

      if (existing.some((sp) => sp.company === row.company?.pk && normCode(sp.sku) === normCode(row.sku))) {
        return 'same';
      }

      return existing.some((sp) => sp.company === row.company?.pk) ? 'supplier' : null;
    },
    [targetPart],
  );

  // Value the existing part already has for a template (undefined if missing)
  const existingValue = useCallback(
    (template: string): string | undefined => targetPart?.parameters?.[template.toLowerCase()],
    [targetPart],
  );

  // Existing part: only the mapped parameters which are missing or set to a placeholder ('-')
  // (the unmapped ones may duplicate data the part already has under another name); new part: all.
  // The other existing values are replaced only when the user selects them
  const defaultInclude = useCallback(
    (p: ParameterRow) => {
      if (!targetPart) {
        return true;
      }

      const current = existingValue(p.template);
      return p.mapped && (current === undefined || isPlaceholderValue(current));
    },
    [targetPart, existingValue],
  );

  // When the target changes, select only the supplier parts the part does not have
  useEffect(() => {
    setForm((f) => ({
      ...f,
      supplier_parts: f.supplier_parts.map((row, i) => ({
        ...row,
        include: targetPart ? presence(row) === null && !row.discontinued : preview.supplier_parts[i].include,
      })),
    }));

    // The parameters are mapped with the category of the part: the one of the existing part,
    // or the one chosen in the form for a new part
    const category = targetPart ? targetPart.category : form.part.category;
    const newCategory = targetPart ? '' : form.part.new_category;

    if (!category && !newCategory) {
      setForm((f) => ({ ...f, parameters: f.parameters.map((p) => ({ ...p, include: defaultInclude(p) })) }));
      return;
    }

    context.api
      .get(`${API_ROOT}/proposal/`, { params: { token: preview.token, category, new_category: newCategory } })
      .then((response) => {
        const proposal: Proposal = response.data;
        setForm((f) => ({
          ...f,
          // Updating an existing part: only the mapped parameters, the others may duplicate
          // data the part already has under another name
          parameters: proposal.parameters.map((p) => ({ ...p, include: defaultInclude(p) })),
        }));
      })
      .catch((err) => notifications.show({ title: 'Import', message: errorMessage(err), color: 'red' }));
    // form.part.category is handled by changeCategory: only the target part triggers this
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetPart, presence, defaultInclude, preview.supplier_parts, preview.token, context.api]);

  const setSupplierPart = (index: number, include: boolean) =>
    setForm((f) => ({
      ...f,
      supplier_parts: f.supplier_parts.map((s, i) => (i === index ? { ...s, include } : s)),
    }));

  const submit = useCallback(() => {
    setSaving(true);
    context.api
      .post(
        `${API_ROOT}/import/`,
        {
          token: form.token,
          part: form.part,
          manufacturer: form.manufacturer,
          mpn: form.mpn,
          // A selected parameter which the part already has replaces the current value
          parameters: form.parameters.map((p) => ({
            ...p,
            overwrite: p.include && existingValue(p.template) !== undefined,
          })),
          supplier_parts: form.supplier_parts,
          existing_part: targetPart ? targetPart.pk : null,
          create_duplicate: !targetPart && preview.existing_parts.length > 0,
          add_missing_parameters: addMissingParameters,
          remember_category: rememberCategory,
          download_image: downloadImage,
          attach_datasheet: attachDatasheet,
          attach_declarations: attachDeclarations,
        },
        { timeout: LONG_TIMEOUT },
      )
      .then((response) => {
        const result = response.data;
        notifications.show({
          title: result.created ? 'Part created' : 'Supplier parts added',
          message: [result.supplier_parts.join(', '), ...result.warnings].filter(Boolean).join(' - '),
          color: result.warnings.length ? 'orange' : 'green',
          autoClose: result.warnings.length ? false : 5000,
        });
        context.navigate(`/part/${result.pk}/`);
        onDone();
      })
      .catch((err) => notifications.show({ title: 'Import', message: errorMessage(err), color: 'red' }))
      .finally(() => setSaving(false));
  }, [
    context,
    form,
    targetPart,
    existingValue,
    preview.existing_parts,
    addMissingParameters,
    rememberCategory,
    downloadImage,
    attachDatasheet,
    attachDeclarations,
    onDone,
  ]);

  const newPart = !useExisting;

  return (
    <Stack gap='md'>
      {form.warnings.map((w) => (
        <Alert key={w} color='orange'>
          {w}
        </Alert>
      ))}

      {preview.existing_parts.length > 0 && (
        <Alert color='blue' title='Already in InvenTree'>
          <Radio.Group value={target} onChange={setTarget}>
            <Stack gap='xs'>
              {preview.existing_parts.map((p) => (
                <Radio
                  key={p.pk}
                  value={String(p.pk)}
                  label={
                    <Text size='sm'>
                      Update{' '}
                      <Anchor size='sm' onClick={() => context.navigate(`/part/${p.pk}/`)}>
                        {p.IPN || '-'} {p.name}
                      </Anchor>{' '}
                      ({MATCH_LABELS[p.match] ?? `same ${p.match}`}
                      {p.active ? '' : ', inactive'}) - suppliers:{' '}
                      {p.supplier_parts.map((sp) => `${sp.supplier} ${sp.sku}`).join(', ') || 'none'}
                    </Text>
                  }
                />
              ))}
              <Radio value='new' label='Create a new part anyway (duplicate)' />
            </Stack>
          </Radio.Group>
          {useExisting && (
            <Checkbox
              mt='sm'
              label='Also update the parameters and add the missing attachments (existing values are replaced only when selected)'
              checked={addMissingParameters}
              onChange={(e) => setAddMissingParameters(e.currentTarget.checked)}
            />
          )}
        </Alert>
      )}

      {newPart && (
        <Paper withBorder p='md'>
          <Group align='flex-start' wrap='nowrap'>
            {form.part.image_url && (
              <Stack gap={4} align='center'>
                <Image src={form.part.image_url} w={120} h={120} fit='contain' />
                <Checkbox
                  size='xs'
                  label='Image'
                  checked={downloadImage}
                  onChange={(e) => setDownloadImage(e.currentTarget.checked)}
                />
              </Stack>
            )}
            <Stack gap='xs' style={{ flex: 1 }}>
              <Group grow>
                <TextInput
                  label='Name'
                  value={form.part.name}
                  onChange={(e) => setPart('name', e.currentTarget.value)}
                  required
                />
                <TextInput
                  label='IPN'
                  value={form.part.IPN}
                  onChange={(e) => setPart('IPN', e.currentTarget.value)}
                  description={form.part.IPN ? 'Proposed: first free number of the category' : undefined}
                />
                <TextInput
                  label='Revision'
                  value={form.part.revision}
                  onChange={(e) => setPart('revision', e.currentTarget.value)}
                />
              </Group>
              <TextInput
                label='Description'
                value={form.part.description}
                onChange={(e) => setPart('description', e.currentTarget.value)}
              />
              <Select
                label='Category'
                data={categories}
                value={form.part.category ? String(form.part.category) : null}
                onChange={changeCategory}
                searchable
                required
                placeholder={form.part.new_category ? `New: ${form.part.new_category}` : undefined}
                description={`${form.parameter_source} category: ${form.supplier_category.join(' / ') || '-'}`}
              />
              {form.part.new_category && !form.part.category && (
                <Group gap='xs'>
                  <Badge color='yellow' variant='light'>
                    new
                  </Badge>
                  <Text size='sm'>
                    <b>{form.part.new_category.split('/').join(' / ')}</b> does not exist yet: it will be created with
                    the part. Choose another category to use an existing one.
                  </Text>
                </Group>
              )}
              {!preview.category_proposed && (
                <Checkbox
                  label={`Remember: use this category for "${form.supplier_category.at(-1) ?? ''}"`}
                  checked={rememberCategory}
                  onChange={(e) => setRememberCategory(e.currentTarget.checked)}
                  disabled={!form.supplier_category.length}
                />
              )}
              <Text size='sm'>
                Manufacturer: <b>{form.manufacturer}</b> - MPN: <b>{form.mpn}</b>
              </Text>
              {form.part.datasheet_url && (
                <Checkbox
                  label={
                    <Anchor href={form.part.datasheet_url} target='_blank' rel='noreferrer' size='sm'>
                      Attach the datasheet link
                    </Anchor>
                  }
                  checked={attachDatasheet}
                  onChange={(e) => setAttachDatasheet(e.currentTarget.checked)}
                />
              )}
            </Stack>
          </Group>
        </Paper>
      )}

      <Title order={5}>Supplier parts</Title>
      <Text size='xs' c='dimmed'>
        Optional: without supplier parts only the part data, parameters and attachments are saved.
      </Text>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th />
            <Table.Th>Supplier</Table.Th>
            <Table.Th>SKU</Table.Th>
            <Table.Th>Packaging</Table.Th>
            <Table.Th>Stock</Table.Th>
            <Table.Th>Price @ 1</Table.Th>
            <Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {form.supplier_parts.map((s, i) => (
            <Table.Tr key={`${s.supplier}-${s.sku}`}>
              <Table.Td>
                <Checkbox
                  checked={s.include}
                  onChange={(e) => setSupplierPart(i, e.currentTarget.checked)}
                  disabled={!s.company || presence(s) === 'same'}
                />
              </Table.Td>
              <Table.Td>
                {s.company?.name ?? `${s.supplier_name} (no InvenTree company)`}{' '}
                {s.primary && <Badge size='xs'>selected</Badge>}{' '}
                {presence(s) === 'same' && (
                  <Badge size='xs' color='gray'>
                    already on the part
                  </Badge>
                )}
                {presence(s) === 'supplier' && (
                  <Badge size='xs' color='yellow' title='The part has this supplier with another SKU'>
                    supplier already on the part
                  </Badge>
                )}
              </Table.Td>
              <Table.Td>
                <Anchor href={s.product_url} target='_blank' rel='noreferrer' size='sm'>
                  {s.sku}
                </Anchor>
              </Table.Td>
              <Table.Td>{s.packaging}</Table.Td>
              <Table.Td>{s.stock ?? '-'}</Table.Td>
              <Table.Td>{formatPrice(s.unit_price, s.currency)}</Table.Td>
              <Table.Td>
                {s.status && (
                  <Badge color={s.discontinued ? 'orange' : 'gray'} variant='light'>
                    {s.status}
                  </Badge>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      {(newPart || addMissingParameters) && (
        <>
          <Title order={5}>Parameters (from {form.parameter_source})</Title>
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th />
                <Table.Th>Template</Table.Th>
                <Table.Th>Value</Table.Th>
                <Table.Th>Supplier name</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {form.parameters.map((p, i) => (
                <Table.Tr key={`${p.source}-${p.template}`}>
                  <Table.Td>
                    <Checkbox
                      checked={p.include}
                      onChange={(e) => setParameter(i, 'include', e.currentTarget.checked)}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap='nowrap'>
                      <TextInput
                        size='xs'
                        value={p.template}
                        onChange={(e) => setParameter(i, 'template', e.currentTarget.value)}
                      />
                      {existingValue(p.template) !== undefined && (
                        <Badge
                          size='xs'
                          color={p.include ? 'orange' : 'gray'}
                          variant='light'
                          title={
                            p.include
                              ? 'The current value will be replaced'
                              : 'The part already has this parameter: select it to replace the value'
                          }
                        >
                          {p.include ? 'replaces' : 'already set'}: {existingValue(p.template) || '-'}
                        </Badge>
                      )}
                      {!p.template_exists && (
                        <Badge size='xs' color='yellow' variant='light' title='The template will be created'>
                          new
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <TextInput
                      size='xs'
                      value={p.value}
                      onChange={(e) => setParameter(i, 'value', e.currentTarget.value)}
                      rightSection={
                        p.units ? (
                          <Text size='xs' c='dimmed'>
                            {p.units}
                          </Text>
                        ) : undefined
                      }
                      rightSectionWidth={p.units ? 40 : undefined}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Text size='xs' c={p.mapped ? undefined : 'dimmed'}>
                      {p.source}
                      {!p.mapped && ' (not mapped)'}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}

      {form.declarations.length > 0 && (
        <Checkbox
          label={`Attach the RoHS / REACH declarations: ${form.declarations.map((d) => d.title).join(', ')}`}
          checked={attachDeclarations}
          onChange={(e) => setAttachDeclarations(e.currentTarget.checked)}
        />
      )}

      <Group justify='flex-end'>
        <Button variant='default' onClick={onDone}>
          Cancel
        </Button>
        <Button onClick={submit} loading={saving} disabled={newPart && !form.part.category && !form.part.new_category}>
          {newPart ? 'Create part' : 'Update part'}
        </Button>
      </Group>
    </Stack>
  );
}

function ImportPage({ context }: { context: InvenTreePluginContext }) {
  const [status, setStatus] = useState<{ available: boolean; error?: string; suppliers?: SupplierInfo[] } | null>(null);
  const [supplier, setSupplier] = useState<string>('');
  const [term, setTerm] = useState<string>('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [messages, setMessages] = useState<string[]>([]);
  const [searched, setSearched] = useState<string>('');
  const [searching, setSearching] = useState<boolean>(false);
  const [selecting, setSelecting] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);

  const loadStatus = useCallback(() => {
    context.api
      .get(`${API_ROOT}/status/`, { timeout: LONG_TIMEOUT })
      .then((response) => {
        setStatus(response.data);
        setSupplier((s) => s || (response.data.suppliers?.length ? 'all' : ''));
      })
      .catch((err) => setStatus({ available: false, error: errorMessage(err) }));
  }, [context.api]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const runSearch = useCallback(() => {
    if (!term.trim() || !supplier) {
      return;
    }

    setSearching(true);
    setPreview(null);
    context.api
      .get(`${API_ROOT}/search/`, { params: { supplier, q: term.trim() }, timeout: LONG_TIMEOUT })
      .then((response) => {
        setResults(response.data.results);
        setMessages(response.data.messages ?? []);
        setSearched(term.trim());
      })
      .catch((err) => notifications.show({ title: 'Search', message: errorMessage(err), color: 'red' }))
      .finally(() => {
        setSearching(false);
        loadStatus();
      });
  }, [context.api, supplier, term, loadStatus]);

  const select = useCallback(
    (result: SearchResult) => {
      setSelecting(`${result.supplier}-${result.sku}`);
      context.api
        .get(`${API_ROOT}/preview/`, {
          params: { supplier: result.supplier, sku: result.sku, q: searched },
          timeout: LONG_TIMEOUT,
        })
        .then((response) => setPreview(response.data))
        .catch((err) => notifications.show({ title: 'Import', message: errorMessage(err), color: 'red' }))
        .finally(() => {
          setSelecting(null);
          loadStatus();
        });
    },
    [context.api, loadStatus, searched],
  );

  const supplierData = useMemo(
    () => [
      { value: 'all', label: 'All' },
      ...(status?.suppliers ?? []).map((s) => ({
        value: s.key,
        label: `${s.name} (${s.remaining_requests})`,
      })),
    ],
    [status],
  );

  if (status === null) {
    return <Loader />;
  }

  if (!status.available) {
    return (
      <Alert color='red' title='Supplier Part Import'>
        {status.error}
      </Alert>
    );
  }

  if (!status.suppliers?.length) {
    return (
      <Alert color='orange' title='Supplier Part Import'>
        No supplier API configured: set the credentials in the settings of the "Part Pricing Update" plugin.
      </Alert>
    );
  }

  return (
    <Stack gap='md' p='md'>
      <Title order={3}>Import from supplier</Title>
      <Group align='flex-end'>
        <SegmentedControl data={supplierData} value={supplier} onChange={setSupplier} />
        <TextInput
          placeholder='Supplier code, MPN or keywords'
          value={term}
          onChange={(e) => setTerm(e.currentTarget.value)}
          onKeyDown={(e) => e.key === 'Enter' && runSearch()}
          style={{ flex: 1 }}
        />
        <Button onClick={runSearch} loading={searching}>
          Search
        </Button>
      </Group>
      <Text size='xs' c='dimmed'>
        The number next to each supplier is the requests left today (shared with the price updates). "All" searches
        every supplier: one request each.
      </Text>

      {!preview && messages.length > 0 && (
        <Alert
          color={results.length ? 'gray' : 'orange'}
          title={results.length ? undefined : `No product to import for "${searched}"`}
        >
          <Stack gap={2}>
            {messages.map((m) => (
              <Text key={m} size='sm'>
                {m}
              </Text>
            ))}
            {!results.length && supplier !== 'all' && <Text size='sm'>Try another supplier, or "All".</Text>}
          </Stack>
        </Alert>
      )}

      {preview ? (
        <PreviewForm context={context} preview={preview} onDone={() => setPreview(null)} />
      ) : (
        results.length > 0 && <SearchResults results={results} onSelect={select} loading={selecting} />
      )}
    </Stack>
  );
}

// Called by InvenTree to render the plugin page
export function renderImportPage(context: InvenTreePluginContext) {
  checkPluginVersion(context);
  return <ImportPage context={context} />;
}
